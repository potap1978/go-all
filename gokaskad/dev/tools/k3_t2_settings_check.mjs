// k3_t2_settings_check.mjs — круг 1, шаг Т2, часть «Настройки» (Claude Design it1 Д3, кадры Н1–Н6, Т3–Т4) в настоящем
// Chrome на заглушке панели. tests/test_it1_t2_settings.py читает JSON из stdout. Разделы:
//   gw      — №36: строка под шапкой «Шлюз — это сам сервер…», в карточке повтора нет, пояснения у адреса и сети;
//             П4 / решение 9: по IP — поле, «Сохранить», «Определить сам», при домене — «Подключать устройства по
//             домену» (окно → вид «по домену»), «Вернуть на IP» (окно → снова поле); без домена — предложения нет;
//   site    — П4: в «Домене и сайте» карточки «Адрес для клиентов» нет, последней — строка-ссылка в «Шлюз» (переход);
//             №37: строки «Кто пользуется» — колонка 240 у правого края, метки и переключатели выровнены;
//   wipe    — №41: строка «Удалить историю активности» последней в карточке, «освободится ≈ N» = «Сейчас занято»;
//             окно: Сотрётся · Останется · Освободится; «Отмена» — ничего; двойное нажатие — одно окно и один сухой
//             прогон; удаление — «нечего удалять», кнопка недоступна, «Хранить» включено; «Хранить» выключено — строки нет;
//   err     — 409 (nosqlite, ?charts=0): слова сервера тостом, окна нет;
//   en      — EN: тексты строки и окна;
//   phone   — 390: «Вернуть на IP» и «Удалить историю…» во всю ширину.
// node tools/k3_t2_settings_check.mjs <адрес статики панели> <chrome>
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3t2s-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_t2_settings_check: время вышло"); process.exit(3); }, 300000).unref();
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
async function view(w, h) { await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 500 }); }
// страница заглушки; запросы DELETE /api/gw/activity пишутся в window.__wipes (сухие и настоящие)
async function open(qs, hash, ready) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1${qs || ""}#${hash}` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page]:not([hidden])')`, 20000);
  await js(`(() => { window.__wipes = []; const M = window.KASKAD_MOCK, f = M.fetch; M.fetch = (u, i) => { if (i && i.method === "DELETE" && /\\/api\\/gw\\/activity(\\?|$)/.test(String(u))) window.__wipes.push(String(u).replace(/^.*\\/api/, "/api")); return f(u, i); }; return true; })()`);
  if (ready) await until(ready, 20000);
  await sleep(400);
}
const T = (sel) => js(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); return n ? n.textContent.trim() : null; })()`);
const dlg = `(() => { const d = [...document.querySelectorAll('.gx-dlg')].pop(); if (!d) return null; return { title: (d.querySelector('.gx-dlg__title') || {}).textContent || "",
  text: d.textContent, heads: [...d.querySelectorAll('.kk-dlg3__h')].map((x) => x.textContent), items: [...d.querySelectorAll('.kk-dlg3__sec')].map((s) => [...s.querySelectorAll('li')].map((x) => x.textContent)),
  btns: [...d.querySelectorAll('.gx-dlg__foot .ga-btn')].map((b) => b.textContent.trim()), n: document.querySelectorAll('.gx-dlg').length, kk: d.classList.contains('kk-dlg3') }; })()`;
const press = (label) => js(`(() => { const d = [...document.querySelectorAll('.gx-dlg')].pop(); const b = [...d.querySelectorAll('.gx-dlg__foot .ga-btn')].find((x) => x.textContent.trim() === ${JSON.stringify(label)}); b.click(); return true; })()`);
const closeAll = () => js(`(() => { let n = 0; while (document.querySelector('.gx-dlg, .gx-sheet') && n++ < 5) document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);
const out = {};
await view(1440, 900);

// ── №36, №39, П4: «Шлюз» ──
{
  const r = {};
  await open("&lang=ru&site=le", "settings?tab=gateway", "!!document.querySelector('.kk-gw2__addr .gx-input')");
  r.lead = await T("#st-part > .kk-phead__s");
  r.cardDesc = await js(`document.querySelectorAll('#stGw .gx-card__tt > .gx-desc').length`);
  r.headChip = await T("#stGw > .gx-card__head .gx-chip");
  r.ip = await js(`(() => { const a = document.querySelector('.kk-gw2__addr'); return { chip: a.querySelector('.kk-gw2__lh .gx-chip').textContent, label: a.querySelector('.kk-gw2__lh .gx-field__l').textContent,
    find: a.getAttribute('data-find'), value: a.querySelector('.gx-input').value, btns: [...a.querySelectorAll('.gx-field__row .ga-btn')].map((b) => b.textContent.trim()),
    hint: a.querySelector(':scope > .gx-hint').textContent, alt: a.querySelector('.kk-gw2__alt') ? a.querySelector('.kk-gw2__alt').textContent : null, dom: !!a.querySelector('.kk-gw2__dom') }; })()`);
  r.net = await js(`(() => { const n = document.querySelector('.kk-gw2__net'); return n ? { label: n.children[0].textContent, vals: [...n.querySelectorAll('.kk-gw2__nv')].map((x) => x.textContent), hint: n.querySelector('.gx-hint').textContent } : null; })()`);
  // «Подключать устройства по домену» → окно (siteHost) → вид «по домену»
  await js(`document.querySelector('.kk-gw2__alt .ga-btn').click(), true`);
  await until(`!!document.querySelector('.gx-dlg')`);
  r.toDomDlg = (await js(dlg)).title;
  await js(`document.querySelector('.gx-dlg__foot .ga-btn--primary').click(), true`);
  await until(`!!document.querySelector('.kk-gw2__dom')`, 15000); await sleep(600); await closeAll(); await sleep(300);
  r.dom = await js(`(() => { const a = document.querySelector('.kk-gw2__addr'); return { chip: a.querySelector('.kk-gw2__lh .gx-chip').textContent, dn: a.querySelector('.kk-gw2__dn').textContent,
    p: a.querySelector('.kk-gw2__dom .gx-p').textContent, btn: a.querySelector('.kk-gw2__dom .ga-btn').textContent.trim(), input: !!a.querySelector('.gx-input'), hint: a.querySelector(':scope > .gx-hint').textContent }; })()`);
  // «Вернуть на IP» → окно (siteHostIp) → снова поле
  await js(`document.querySelector('.kk-gw2__dom .ga-btn').click(), true`);
  await until(`!!document.querySelector('.gx-dlg')`);
  const d = await js(dlg);
  r.toIpDlg = { title: d.title, hasKeep: /останется домен/.test(d.text) };
  await press("Вернуть на IP");
  await until(`!!document.querySelector('.kk-gw2__addr .gx-input') && !document.querySelector('.kk-gw2__dom')`, 15000); await sleep(400); await closeAll();
  r.back = await js(`(() => { const a = document.querySelector('.kk-gw2__addr'); return { chip: a.querySelector('.kk-gw2__lh .gx-chip').textContent, value: a.querySelector('.gx-input').value, alt: !!a.querySelector('.kk-gw2__alt') }; })()`);
  // без домена — предложения нет
  await open("&lang=ru", "settings?tab=gateway", "!!document.querySelector('.kk-gw2__addr .gx-input')");
  r.noDom = await js(`({ alt: !!document.querySelector('.kk-gw2__alt'), dom: !!document.querySelector('.kk-gw2__dom') })`);
  // «Изменить» с «Обзора» (#settings?open=host) при домене — тоже «Шлюз», подсвечен адрес
  await open("&lang=ru&site=sstp", "settings?open=host", "!!document.querySelector('.kk-gw2__addr')");
  await sleep(500);
  r.openHost = await js(`({ part: document.querySelector('#st-part').dataset.part, hash: location.hash })`);
  out.gw = r;
}

// ── П4, №37: «Домен и сайт» ──
{
  const r = {};
  await open("&lang=ru&site=sstp", "settings?tab=site", "!!document.querySelector('.kk-lrow') && !!document.querySelector('.kk-use')");
  r.oldCard = await js(`!!document.querySelector('#siteHost, [data-find="settings.siteHost"]')`);
  r.lrow = await js(`(() => { const a = document.querySelector('.kk-lrow'); const part = document.querySelector('#st-part'); const all = [...part.querySelectorAll('.gx-card')];
    return { t: a.querySelector('.kk-lrow__t').textContent, s: a.querySelector('.kk-lrow__s').textContent, href: a.getAttribute('href'), tag: a.tagName, last: all[all.length - 1] === a }; })()`);
  r.use = await js(`(() => { const rows = [...document.querySelectorAll('.kk-use__r')]; const card = document.querySelector('.kk-use').closest('.gx-card');
    const cr = card.getBoundingClientRect(), pad = parseFloat(getComputedStyle(card).paddingRight);
    return { n: rows.length, cols: getComputedStyle(rows[0]).gridTemplateColumns.split(' ').pop(), titles: rows.map((x) => x.querySelector('.kk-use__b > b').textContent),
      rights: rows.map((x) => Math.round(x.querySelector('.kk-use__e').lastElementChild.getBoundingClientRect().right)), cardRight: Math.round(cr.right - pad),
      sw: rows.slice(2).map((x) => { const b = x.querySelector('button[role=switch]'); return b ? { aria: b.getAttribute('aria-label'), on: b.getAttribute('aria-checked') } : null; }),
      chips: rows.slice(0, 2).map((x) => (x.querySelector('.kk-use__e .gx-chip') || {}).textContent || null) }; })()`);
  // переключатель OpenConnect — то же окно с сухим прогоном (siteUse); «Отмена» — без изменений
  await js(`document.querySelectorAll('.kk-use__r')[3].querySelector('button[role=switch]').click(), true`);
  await until(`!!document.querySelector('.gx-dlg')`);
  r.ocDlg = (await js(dlg)).title;
  await press("Отмена"); await sleep(300);
  r.ocAfter = await js(`document.querySelectorAll('.kk-use__r')[3].querySelector('button[role=switch]').getAttribute('aria-checked')`);
  // строка-ссылка ведёт в «Шлюз»
  await js(`document.querySelector('.kk-lrow').click(), true`);
  await until(`document.querySelector('#st-part') && document.querySelector('#st-part').dataset.part === 'gateway'`, 15000);
  r.go = await js(`({ part: document.querySelector('#st-part').dataset.part, addr: !!document.querySelector('.kk-gw2__dom') })`);
  out.site = r;
}

// ── №41: «Хранение данных» ──
{
  const r = {};
  await open("&lang=ru", "settings?tab=storage", "!!document.querySelector('.kk-wipe')");
  const row = `(() => { const w = document.querySelector('.kk-wipe'); if (!w) return null; const card = w.closest('.gx-card'); const b = w.querySelector('.ga-btn');
    const kv = [...card.querySelectorAll('.gx-kv__row, .gx-kv > *')].map((x) => x.textContent); const shown = [...card.children].filter((x) => x.getClientRects().length > 0); return { last: shown[shown.length - 1] === w, title: w.querySelector('.kk-wipe__b > b').textContent,
    btn: b.textContent.trim(), cls: b.className, dis: b.disabled, desc: b.getAttribute('aria-describedby'), free: w.querySelector('.kk-wipe__f').textContent, kv: kv.join(' | '),
    keep: card.querySelector('.gx-sw').getAttribute('aria-checked') }; })()`;
  r.before = await js(row);
  await js(`window.__wipes = [], true`);
  // двойное нажатие: одно окно, один сухой прогон
  await js(`(() => { const b = document.querySelector('.kk-wipe .ga-btn'); b.click(); b.click(); return true; })()`);
  await until(`!!document.querySelector('.kk-dlg3')`); await sleep(400);
  r.dlg = await js(dlg);
  r.wipesAsk = await js(`window.__wipes.slice()`);
  await press("Отмена"); await sleep(400);
  r.cancel = { wipes: await js(`window.__wipes.slice()`), row: await js(row) };
  // удалить
  await js(`document.querySelector('.kk-wipe .ga-btn').click(), true`);
  await until(`!!document.querySelector('.kk-dlg3')`); await sleep(200);
  await press("Удалить историю");
  await until(`(() => { const f = document.querySelector('.kk-wipe__f'); return f && f.textContent === 'нечего удалять'; })()`, 15000); await sleep(300);
  r.after = await js(row);
  r.wipes = await js(`window.__wipes.slice()`);
  r.toast = await js(`[...document.querySelectorAll('.gx-toast')].map((x) => x.textContent).join(' | ')`);
  // «Хранить» выключено (черновик, ещё не сохранено) — строки нет
  await open("&lang=ru", "settings?tab=storage", "!!document.querySelector('.kk-wipe')");
  await js(`document.querySelector('.kk-sto2 .gx-sw').click(), true`); await sleep(300);
  r.draftOff = await js(`!!document.querySelector('.kk-wipe')`);
  // сервер с выключенной историей
  await open("&lang=ru&act=off", "settings?tab=storage", "!!document.querySelector('.kk-sto2 .gx-sw')");
  r.off = await js(`!!document.querySelector('.kk-wipe')`);
  out.wipe = r;
}

// ── 409: нет sqlite3 — слова сервера тостом, окна нет ──
{
  await open("&lang=ru&charts=0", "settings?tab=storage", "!!document.querySelector('.kk-wipe')");
  await js(`document.querySelector('.kk-wipe .ga-btn').click(), true`);
  await until(`!!document.querySelector('.gx-toast')`, 10000); await sleep(300);
  out.err = { toast: await js(`[...document.querySelectorAll('.gx-toast')].map((x) => x.textContent).join(' | ')`), dlg: await js(`!!document.querySelector('.gx-dlg')`), wipes: await js(`window.__wipes.slice()`) };
}

// ── EN ──
{
  await open("&lang=en", "settings?tab=storage", "!!document.querySelector('.kk-wipe')");
  const r = { row: await js(`(() => { const w = document.querySelector('.kk-wipe'); return { title: w.querySelector('.kk-wipe__b > b').textContent, btn: w.querySelector('.ga-btn').textContent.trim(), free: w.querySelector('.kk-wipe__f').textContent }; })()`) };
  await js(`document.querySelector('.kk-wipe .ga-btn').click(), true`);
  await until(`!!document.querySelector('.kk-dlg3')`); await sleep(200);
  r.dlg = await js(dlg);
  await closeAll();
  await open("&lang=en", "settings?tab=gateway", "!!document.querySelector('.kk-gw2__addr')");
  r.lead = await T("#st-part > .kk-phead__s");
  out.en = r;
}

// ── телефон 390 ──
{
  await view(390, 844);
  const r = {};
  await open("&lang=ru&site=sstp", "settings?tab=gateway", "!!document.querySelector('.kk-gw2__dom .ga-btn')");
  r.ip = await js(`(() => { const t = document.querySelector('.kk-gw2__dom'), b = t.querySelector('.ga-btn'); const pl = parseFloat(getComputedStyle(t).paddingLeft) + parseFloat(getComputedStyle(t).paddingRight);
    return { btn: Math.round(b.getBoundingClientRect().width), inner: Math.round(t.getBoundingClientRect().width - pl), over: document.documentElement.scrollWidth > innerWidth }; })()`);
  await open("&lang=ru", "settings?tab=storage", "!!document.querySelector('.kk-wipe .ga-btn')");
  r.wipe = await js(`(() => { const e = document.querySelector('.kk-wipe__e'), b = e.querySelector('.ga-btn'); return { btn: Math.round(b.getBoundingClientRect().width), inner: Math.round(e.getBoundingClientRect().width), over: document.documentElement.scrollWidth > innerWidth }; })()`);
  // «Кто пользуется»: переключатель своего размера 40×24, метка не вылезает за карточку
  await open("&lang=ru&site=sstp", "settings?tab=site", "!!document.querySelector('.kk-use')");
  r.use = await js(`(() => { const rows = [...document.querySelectorAll('.kk-use__r')], card = rows[0].closest('.gx-card').getBoundingClientRect(); const sw = rows[2].querySelector('button[role=switch]').getBoundingClientRect();
    return { sw: [Math.round(sw.width), Math.round(sw.height)], inside: rows.every((x) => x.querySelector('.kk-use__e').lastElementChild.getBoundingClientRect().right <= card.right - 8), over: document.documentElement.scrollWidth > innerWidth }; })()`);
  out.phone = r;
}
console.log(JSON.stringify(out));
stop();
process.exit(0);
