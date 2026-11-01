// k3_people_check.mjs — UI-4: «Люди» таблицей К1 (общий блок gx-xt, xt.js) в настоящем Chrome на заглушке панели.
// Что проверяется (tests/test_ui4_people.py читает JSON из stdout):
//   toggle   — свёрнуто по умолчанию; нажатие на кнопку строки и по свободному месту строки раскрывает (aria-expanded,
//              подстроки видны), повтор сворачивает; Enter и пробел на кнопке; стрелка вниз — к следующей строке;
//   memory   — раскрытый человек остаётся раскрытым после перерисовки (живые данные) и после перезагрузки;
//   four     — человек с 4 устройствами раскрыт: 4 строки устройств, строки «Добавить устройство» нет (эталон 23.10);
//   noDevAdd — человек без устройств раскрыт: одна строка «Добавить устройство»;
//   density  — «Крупно · Средне · Строка» (role=radio): класс kk-dn--* на обёртке, запомнено в браузере, после
//              перезагрузки тот же; стрелки внутри переключателя;
//   menus    — «⋯» человека и устройства: подписи пунктов;
//   actions  — каждый пункт «⋯» нажат: какое окно открылось (подпись окна), куда перешли, что скопировано — то же
//              действие, что раньше (ни одно прежнее действие не потеряно и не подменено; REV-ui4 З-8);
//   arrows   — стрелки вниз/вверх по людям и строкам раскрытых устройств без застревания (З-1);
//   order    — протоколы: места по порядку узла, от порядка выдачи доступа не зависят; X одного протокола во всех
//              строках ±2 px, плашки не обрезаны (С-1 и замечание владельца 20.10);
//   rowgrid  — «Строка»: колонками сверху вниз (эталон 23.10); при раскрытии люди не переходят в другую колонку (З-2),
//              устройства — под своим человеком; состояние устройства — точкой (З-3);
//   narrow   — фильтр «Не в сети» в подменю компьютера → планшет 1024 (ряд кнопок) и телефон 390 (кнопка «Показать» и
//              лист, nav2 r1): выбранный виден, адрес #people?show=off, «Все» его снимает (З-4);
//   aria     — подписи для диктора с именем (З-7); причина недоступного переключателя, «устройства выключены» (П-3);
//   longname — длинное имя: метка проблемы не сжимается, обрезается имя (З-6);
//   give     — «Выдать настройки» человека: несколько устройств — меню «Кому выдать настройки» с устройствами, одно —
//              сразу окно выдачи; выключенный человек — кнопка недоступна; О5 / t4: заголовок «Как подключить «…»?» и
//              подстрока за устройством, ряд «Платформа» (по умолчанию — ОС устройства, выбор переживает смену вкладки),
//              приложения подписки списком give.js, пометка в подвале; то же по-английски (giveEn);
//   search   — запрос раскрывает совпавших, совпадение подсвечено; закрытый вручную остаётся закрытым до смены запроса;
//   problem  — метка у имени выключенного человека; переключатели устройств выключенного человека недоступны;
//   focus    — переход #people?focus=<устройство>: его человек раскрыт, строка устройства на месте;
//   empty    — пустой узел: рамка таблицы с «Добавить первого человека», фильтров и поиска нет;
//   en       — EN: шапка таблицы и переключатель плотности по-английски;
// node tools/k3_people_check.mjs <адрес статики панели> <chrome>
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3ppl-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_people_check: время вышло"); process.exit(3); }, 240000).unref();
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
async function open(qs, hash = "people", fresh = true) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  // still=1: «в сети» устройств не меняется само — проверки ждут точные состояния (REV-3377-ui, нестабильный З-7)
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1${qs || ""}#${hash}` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page="people"]:not([hidden])')`, 20000);
  if (fresh) { await js(`(sessionStorage.clear(), localStorage.removeItem("kaskad-density-people"), true)`); await cdp("Page.reload"); await sleep(300); }
  await until(`!!document.querySelector('.gx-xt__g, .gx-xt__empty')`, 20000);
  await sleep(400);
}
// группа человека по имени; состояние раскрытия
const G = (name) => `[...document.querySelectorAll('.gx-xt__g')].find((g) => g.querySelector('.kk-pname').textContent === ${JSON.stringify(name)})`;
const ST = (name) => js(`(() => { const g = ${G(name)}; if (!g) return null; const x = g.querySelector('.gx-xt__x'), s = g.querySelector('.gx-xt__sub');
  return { exp: x.getAttribute('aria-expanded'), open: g.classList.contains('is-open'), hidden: s.hidden, ctl: x.getAttribute('aria-controls') === s.id,
    devs: s.querySelectorAll('.gx-xt__r--sub').length, add: s.querySelectorAll('.gx-xt__r--add').length, shown: s.offsetHeight > 0 }; })()`);
const menu = async (sel) => {
  await js(`(() => { const b = ${sel}; b.click(); return true; })()`);
  await until(`!!document.querySelector('#layers .gx-menu')`, 3000);
  const items = await js(`[...document.querySelectorAll('#layers .gx-menu__item')].map((b) => b.querySelector('span:not(.gi):not(.gx-menu__k)').textContent + (b.querySelector('.gx-menu__k') ? '=' + b.querySelector('.gx-menu__k').textContent : ''))`);
  await key("Escape", "Escape");
  await until(`!document.querySelector('#layers .gx-menu')`, 3000);
  return items;
};
const out = {};

// ── раскрытие, клавиатура, память ──
await open("&lang=ru");
{
  const r = { n: await js(`document.querySelectorAll('.gx-xt__g').length`), s0: await ST("Тест G3") };
  await js(`(${G("Тест G3")}).querySelector('.gx-xt__x').click(), true`); r.s1 = await ST("Тест G3");
  await js(`(${G("Тест G3")}).querySelector('.gx-xt__x').click(), true`); r.s2 = await ST("Тест G3");
  // по свободному месту строки (ячейка «Маршрут») — раскрыть; по «⋯» — не раскрывать
  await js(`(${G("Мама")}).querySelector('.gx-xt__r > .gx-xt__c:nth-child(5)').click(), true`); r.rowClick = await ST("Мама");
  await js(`(${G("Мама")}).querySelector('.gx-xt__r > .gx-xt__c:nth-child(5)').click(), true`);
  await js(`(${G("Папа")}).querySelector('.gx-xt__r .gx-menu-btn').click(), true`); await sleep(200);
  r.menuNoToggle = await ST("Папа"); await key("Escape", "Escape");
  // клавиатура: фокус на кнопке строки «Мама», Enter, пробел, стрелка вниз
  await js(`(${G("Мама")}).querySelector('.gx-xt__x').focus(), true`);
  await key("Enter", "Enter", "\r"); r.enter = await ST("Мама");
  await key(" ", "Space", " "); r.space = await ST("Мама");
  await key("ArrowDown", "ArrowDown"); r.down = await js(`document.activeElement.closest('.gx-xt__g').querySelector('.kk-pname').textContent`);
  out.toggle = r;
}
{
  const r = {};
  await js(`(${G("Тест G3")}).querySelector('.gx-xt__x').click(), true`);
  out.four = await ST("Тест G3");
  out.four.names = await js(`[...(${G("Тест G3")}).querySelectorAll('.gx-xt__r--sub .gx-xt__n')].map((n) => n.textContent)`);
  // человек без устройств: раскрыт — одна строка «Добавить устройство»; затем снова свёрнут (память раскрытия ниже)
  await js(`(${G("Гости")}).querySelector('.gx-xt__x').click(), true`); await sleep(150);
  out.noDevAdd = (await ST("Гости") || {}).add;
  await js(`(${G("Гости")}).querySelector('.gx-xt__x').click(), true`); await sleep(150);
  // перерисовка по живым данным (мок шлёт кадры) — раскрытый на месте; затем перезагрузка
  await sleep(4500);
  r.afterLive = await ST("Тест G3");
  await cdp("Page.reload"); await until(`!!document.querySelector('.gx-xt__g')`, 20000); await sleep(500);
  r.afterReload = await ST("Тест G3");
  r.othersOpen = (await ST("Мама")).open;
  out.memory = r;
}
// О5 / t4: шапка, ряд «Платформа», список приложений подписки (give.js), подвал
const GHEAD = `{ title: d.querySelector('.gx-sheet__title').textContent, sub: (d.querySelector('.gx-sheet__sub') || {}).textContent || '',
  plats: [...d.querySelectorAll('.kk-iss__plats > [role=radio]')].map((b) => b.textContent + (b.getAttribute('aria-checked') === 'true' ? '*' : '')).join('|'),
  apps: [...d.querySelectorAll('.kk-iss__apps .gs-app')].map((a) => a.querySelector('.gs-app__n b').textContent + (a.querySelector('.gs-rec') ? '+rec' : '') + (a.querySelector('.gs-app__f') ? ':' + a.querySelector('.gs-app__f').textContent : '')).join('|'),
  fakes: (d.querySelector('.kk-iss__apps .gs-fakes') || {}).textContent || '',
  rowH: Math.max(0, ...[...d.querySelectorAll('.kk-iss__apps .gs-app:not(.is-on)')].map((a) => Math.round(a.getBoundingClientRect().height))),
  rowOnH: Math.max(0, ...[...d.querySelectorAll('.kk-iss__apps .gs-app.is-on')].map((a) => Math.round(a.getBoundingClientRect().height))),
  rows: [...d.querySelectorAll('.kk-iss__apps .gs-app')].map((a) => Math.round(a.getBoundingClientRect().height) + ':' + Math.round(a.getBoundingClientRect().width) + ':' + Math.round(a.querySelector('.gs-app__n').getBoundingClientRect().width) + ':' + Math.round((a.querySelector('.gs-app__acts') || a).getBoundingClientRect().width)).join(' '),
  scroll: (() => { const b = d.querySelector('.gx-dlg__body'); return b ? b.scrollHeight - b.clientHeight : -1; })(),
  appsIn: (() => { const b = d.querySelector('.gx-dlg__body'), r = [...d.querySelectorAll('.kk-iss__apps .gs-app, .kk-iss__ol > li')]; if (!b || !r.length) return false; const bb = b.getBoundingClientRect().bottom; return r.every((x) => x.getBoundingClientRect().bottom <= bb + 0.5); })(),
  foot: (d.querySelector('.gx-dlg__foot .gs-foot') || {}).textContent || '', lock: !!d.querySelector('.gx-dlg__foot .gs-foot .gi'),
  closeRight: (() => { const f = d.querySelector('.gx-dlg__foot'), b = f && f.querySelector(':scope > .ga-btn'); return !!b && b.getBoundingClientRect().right > f.getBoundingClientRect().right - 60; })() }`;
// ── «⋯» и «Выдать настройки» ──
{
  const r = {};
  r.person = await menu(`(${G("Мама")}).querySelector('.gx-xt__r .gx-menu-btn')`);
  r.device = await menu(`(${G("Тест G3")}).querySelector('.gx-xt__r--sub .gx-menu-btn')`);
  out.menus = r;
  const g = {};
  // Т3 (Д2 Б r3, О5/О6): QR → одно окно, вкладки «Подписка» (открыта, рекомендуем) | «Статический ключ»; устройство —
  // плашками, смена вкладки его не сбрасывает; адреса подписки на экране нет до «QR-код» / «Скопировать»
  await js(`(() => { (${G("Мама")}).querySelector('.gx-xt__acts .ga-btn--xs.is-main').click(); return true; })()`);
  g.pick = await until(`!!document.querySelector('[data-qr-pick="static"]')`, 3000).catch(() => false);
  const gst = `(() => { const d = document.querySelector('.kk-iss'); return { tabs: [...d.querySelectorAll('[role=tab]')].map((b) => b.dataset.qrPick + ':' + b.getAttribute('aria-selected')).join(' '),
    devs: [...d.querySelectorAll('.kk-iss__chips > button')].map((b) => b.textContent + (b.getAttribute('aria-pressed') === 'true' ? '*' : '')).join('|'),
    hidden: !!d.querySelector('.kk-subk'), url: !!d.querySelector('.kk-subu'), al: (d.querySelector('.kk-iss__key .gx-al') || {}).textContent || '', give: !!d.querySelector('.gs-host .gs'), steps: d.querySelectorAll('.kk-iss__ol > li').length,
    ...${GHEAD} }; })()`;
  const appsUp = () => until(`!!document.querySelector('.kk-iss .kk-iss__plats > [role=radio][aria-checked=true]') && !!document.querySelector('.kk-iss .kk-iss__apps .gs-app')`, 6000).catch(() => false);
  await appsUp(); await sleep(300); g.s0 = await js(gst);
  await js(`(document.querySelector('[data-qr-pick="static"]').click(), true)`); await until(`!!document.querySelector('.kk-iss .gs-host .gs')`, 6000).catch(() => false); g.s1 = await js(gst);
  // Д-2 (REV-it1-b): окно открылось на «Телефон Мамы» (первое с платформой подписки), не на «Роутер на даче»; смена — на роутер
  await js(`([...document.querySelectorAll('.kk-iss__chips > button')].find((b) => /Роутер/.test(b.textContent)).click(), true)`); await sleep(800); g.s2 = await js(gst);
  await js(`(document.querySelector('[data-qr-pick="sub"]').click(), true)`); await appsUp(); await sleep(300); g.s3 = await js(gst);
  // платформа вручную (Android) — список её приложений; смена вкладки выбор не сбрасывает; стрелка — к соседней
  const plat = (x) => js(`(document.querySelector('.kk-iss__plats [data-plat="${x}"]').click(), true)`);
  await plat("android"); await sleep(400); g.sAnd = await js(gst);
  await js(`(document.querySelector('[data-qr-pick="static"]').click(), true)`); await sleep(600);
  await js(`(document.querySelector('[data-qr-pick="sub"]').click(), true)`); await appsUp(); await sleep(300); g.sBack = await js(gst);
  await js(`(document.querySelector('.kk-iss__plats [aria-checked="true"]').focus(), true)`); await key("ArrowRight", "ArrowRight"); await sleep(300);
  g.sArrow = await js(`(() => { const d = document.querySelector('.kk-iss'), a = document.activeElement; return [(${GHEAD}).plats, a && a.getAttribute('data-plat')]; })()`);
  await js(`([...document.querySelectorAll('.kk-iss__kb > .ga-btn')].pop().click(), true)`); await until(`!!document.querySelector('.kk-iss .kk-subu')`, 6000).catch(() => false); g.s4 = await js(gst);
  await key("Escape", "Escape"); await sleep(400);
  // REV-it1-b С-1: подписка уже включена и адрес известен (панель открыта в интернет) — при новом открытии окна адрес
  // скрыт, пока не нажали «Скопировать» / «QR-код»; закрыли и открыли снова — опять скрыт
  // открытый режим заглушки: вкладка туннеля выходит из входа — тут же вход паролем заглушки (её db, не настоящий)
  g.pub = await js(`(async () => { const M = KASKAD_MOCK, H = { 'X-Kaskad-Admin': '1', 'Content-Type': 'application/json' };
    const a = await M.fetch('/api/panel/public', { method: 'PUT', headers: H, body: JSON.stringify({ on: true }) });
    const b = await M.fetch('/api/login', { method: 'POST', headers: H, body: JSON.stringify({ password: M.db().panel.pw }) });
    return a.status + ' ' + b.status; })()`);
  await sleep(600);
  const giveMama = async () => { await js(`(() => { (${G("Мама")}).querySelector('.gx-xt__acts .ga-btn--xs.is-main').click(); return true; })()`);
    await until(`!!document.querySelector('.kk-iss .kk-subk, .kk-iss .kk-subu')`, 4000).catch(() => false); await sleep(700); return js(gst); };
  const kbBtn = (i) => js(`([...document.querySelectorAll('.kk-iss__kb > .ga-btn')][${i}].click(), true)`);
  g.s5 = await giveMama();
  await kbBtn(1); await until(`!!document.querySelector('.kk-iss .kk-subu')`, 4000).catch(() => false); g.s6 = await js(gst);
  await key("Escape", "Escape"); await sleep(400);
  g.s7 = await giveMama();
  await kbBtn(0); await until(`!!document.querySelector('.kk-iss .kk-subu')`, 4000).catch(() => false); g.s8 = await js(gst);
  g.s8.qr = await js(`!!document.querySelector('.kk-iss .kk-qr--on')`);
  await key("Escape", "Escape"); await sleep(400);
  // доводка круга 1, п.3: «⋯» → «Ссылка для приложений» (подписка Мамы уже включена, адрес известен) — приложения тем
  // же блоком каталога (ряд «Платформа», строки give.js, без треугольников), не текстом; адреса с токеном на экране нет
  // до «QR-код» / «Скопировать»; «QR-код» — адрес и код
  const sbSt = `(() => { const d = [...document.querySelectorAll('.gx-dlg')].filter((x) => x.getClientRects().length).pop(); if (!d) return null;
    return { title: (d.querySelector('.gx-sheet__title') || {}).textContent, apps: d.querySelectorAll('.kk-sb__apps .gs-app').length,
      plat: (d.querySelector('.kk-iss__plats [aria-checked=true]') || {}).textContent || '', tri: d.querySelectorAll('.gs-tri').length,
      oldList: d.textContent.indexOf('v2RayTun, Hiddify') >= 0, hidden: !!d.querySelector('.kk-subk'), url: !!d.querySelector('.gx-code, .kk-code, code'),
      token: new RegExp('/p/[A-Za-z0-9_-]{16,}').test(d.outerHTML + [...d.querySelectorAll('input,textarea')].map((x) => x.value).join(' ')),
      qr: !!d.querySelector('.gx-qrcard'), btns: [...d.querySelectorAll('.ga-btn')].map((b) => b.textContent.trim()).filter(Boolean),
      notes: [...d.querySelectorAll('.gs-app__nt-t')].map((n) => { const lh = parseFloat(getComputedStyle(n).lineHeight) || 18;
        return { id: (n.closest('.gs-app').querySelector('[data-gk^="app:"]') || {}).getAttribute('data-gk'), on: n.closest('.gs-app').classList.contains('is-on'),
          lines: Math.round(n.clientHeight / lh), cut: n.scrollHeight > n.clientHeight + 1 || n.scrollWidth > n.clientWidth + 1 }; }) }; })()`;
  const sbBtn = (re) => js(`(() => { const d = [...document.querySelectorAll('.gx-dlg')].filter((x) => x.getClientRects().length).pop(); const b = [...d.querySelectorAll('.ga-btn')].find((x) => ${re}.test(x.textContent)); if (b) b.click(); return !!b; })()`);
  const sbOpen = async () => { await js(`(${G("Мама")}).querySelector('.gx-xt__r .gx-menu-btn').click(), true`); await until(`!!document.querySelector('#layers .gx-menu')`, 3000);
    await js(`([...document.querySelectorAll('#layers .gx-menu__item')].find((b) => /Ссылка для приложений/.test(b.textContent)).click(), true)`);
    await until(`!!document.querySelector('.kk-sb__apps .gs-app')`, 6000).catch(() => false); await sleep(400); return js(sbSt); };
  g.sb0 = await sbOpen();
  // REV-it1-pol З-3: пометка выбранного приложения — целиком; нажатие на строку с обрезанной пометкой — она целиком
  const cutId = (g.sb0.notes.find((n) => n.cut && !n.on) || {}).id;
  if (cutId) { await js(`([...document.querySelectorAll('.gx-dlg')].pop().querySelector('[data-gk="${cutId}"]').click(), true)`); await sleep(300); }
  g.sbNote = { id: cutId || null, after: (await js(sbSt)).notes.find((n) => n.id === cutId) || null };
  await js(`([...document.querySelectorAll('.gx-dlg')].pop().querySelector('.kk-iss__kb > .ga-btn').click(), true)`); await sleep(900); g.sb1 = await js(sbSt);
  await key("Escape", "Escape"); await sleep(400);
  g.sb2 = await sbOpen();
  // REV-it1-pol З-1: после «Скопировать» кнопка «QR-код» на месте и рисует код
  await js(`(() => { window.__clip = null; try { navigator.clipboard.writeText = (s) => { window.__clip = s; return Promise.resolve(); }; } catch (e) { /* нет */ } return true; })()`);
  await js(`([...document.querySelectorAll('.gx-dlg')].pop().querySelectorAll('.kk-iss__kb > .ga-btn')[1].click(), true)`); await sleep(700);
  g.sb3 = await js(sbSt); g.sb3.clip = await js(`!!window.__clip`);
  await sbBtn("/QR/"); await sleep(900); g.sb3q = await js(sbSt);
  // REV-it1-pol З-2: показанный адрес → «Отозвать ссылку» (подтверждение) → «Создать ссылку…» — новый адрес снова скрыт
  await sbBtn("/Отозвать ссылку/"); await until(`!!document.querySelector('[role=alertdialog]')`, 3000).catch(() => false);
  await js(`([...document.querySelectorAll('[role=alertdialog] .ga-btn')].find((b) => /Отозвать ссылку/.test(b.textContent)).click(), true)`); await sleep(900);
  g.sb4off = await js(sbSt);
  await sbBtn("/Создать ссылку/"); await sleep(900); g.sb4 = await js(sbSt);
  await key("Escape", "Escape"); await sleep(400);
  g.offDis = await js(`(${G("Гости")}).querySelector('.gx-xt__acts .ga-btn--xs.is-main').disabled`);
  g.offTip = await js(`(${G("Гости")}).querySelector('.gx-xt__acts .ga-btn--xs.is-main').title`);
  out.give = g;
}
// ── проблема в строке ──
{
  const r = {};
  r.chip = await js(`(() => { const c = (${G("Гости")}).querySelector('.gx-xt__nl > .gx-chip'); return c ? c.textContent : null; })()`);
  r.offRow = await js(`(${G("Гости")}).querySelector('.gx-xt__r').classList.contains('is-off')`);
  r.sw = await js(`(${G("Гости")}).querySelector('.gx-xt__sw').getAttribute('aria-checked')`);
  out.problem = r;
}
// ── поиск ──
{
  const r = {};
  const type = async (s) => { await js(`(() => { const i = document.querySelector('.kk-pp__bar input[type=search]'); i.value = ${JSON.stringify(s)}; i.dispatchEvent(new Event('input', { bubbles: true })); return true; })()`); await sleep(700); };
  await type("socks");
  r.people = await js(`[...document.querySelectorAll('.gx-xt__g .kk-pname')].map((n) => n.textContent)`);
  r.s = await ST("Тест G3");
  r.hit = await js(`[...document.querySelectorAll('.gx-xt__g mark.gx-hit')].map((m) => m.textContent)`);
  await js(`(${G("Тест G3")}).querySelector('.gx-xt__x').click(), true`); r.closed = await ST("Тест G3");
  await type("socks"); r.sameQuery = await ST("Тест G3");
  await type("sock"); r.newQuery = await ST("Тест G3");
  await type(""); r.cleared = await ST("Тест G3");
  out.search = r;
}
// nav2 r1 / r4 (UI-A1): слева от списка — подменю «Людей» (styles.css .kk-sub: 200 + зазор 24 с 2000, 184 + 24 на 1440–1999,
// 164 + 24 на 1280–1439 — таблица ширин r4, 164 + 20 на 900–1279) — тело таблицы на столько же уже.
// Проверки тела (две колонки «Строки», плашки протоколов) — на окне шире на подменю: тело той же ширины, что было
// до подменю. Узкое тело рядом с подменю (1100–1279, эталон H6 — без колонки «Маршрут») — шаг UI-B (people.css).
const SUBOFF = (x) => (x >= 2000 ? 224 : x >= 1440 ? 208 : x >= 1280 ? 188 : 184);
const BODY = (w) => [224, 208, 188, 184].map((o) => w + o).find((x) => SUBOFF(x) === x - w) || w + 208;
// ── плотность ──
{
  await cdp("Emulation.setDeviceMetricsOverride", { width: BODY(1440), height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(300);
  const r = { def: await js(`document.querySelector('.kk-pp__body').className`) };
  r.radios = await js(`[...document.querySelectorAll('.kk-pp__dn [role=radio]')].map((b) => b.textContent + ':' + b.getAttribute('aria-checked'))`);
  r.group = await js(`document.querySelector('.kk-pp__dn').getAttribute('role')`);
  await js(`document.querySelector('.kk-pp__dn [data-dn=row]').click(), true`);
  r.row = await js(`[document.querySelector('.kk-pp__body').className, localStorage.getItem('kaskad-density-people')]`);
  r.rowCols = await js(`String(getComputedStyle(document.querySelector('.gx-xt__cols')).gridTemplateColumns.split(' ').length)`);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(300);
  await cdp("Page.reload"); await until(`!!document.querySelector('.gx-xt__g')`, 20000); await sleep(400);
  r.reload = await js(`[document.querySelector('.kk-pp__body').className, document.querySelector('.kk-pp__dn [aria-checked=true]').getAttribute('data-dn')]`);
  await js(`document.querySelector('.kk-pp__dn [aria-checked=true]').focus(), true`);
  await key("ArrowLeft", "ArrowLeft");
  r.arrow = await js(`[document.querySelector('.kk-pp__body').className, document.activeElement.getAttribute('data-dn')]`);
  r.mdRow = await js(`Math.round(document.querySelector('.gx-xt__g > .gx-xt__r').getBoundingClientRect().height)`);
  await key("ArrowLeft", "ArrowLeft");
  await key("ArrowLeft", "ArrowLeft");
  r.back = await js(`[document.querySelector('.kk-pp__body').className, localStorage.getItem('kaskad-density-people')]`);
  r.rowH = await js(`Math.round(document.querySelector('.gx-xt__g > .gx-xt__r').getBoundingClientRect().height)`);
  out.density = r;
}
// ── переход к устройству ──
{
  await open("&lang=ru", "people?focus=d-3c3c0003", true);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#people?focus=d-3c3c0003` });
  await until(`!!document.querySelector('.gx-xt__r[data-did="d-3c3c0003"]') && document.querySelector('.gx-xt__r[data-did="d-3c3c0003"]').offsetHeight > 0`, 15000);
  await sleep(600);
  out.focus = { s: await ST("Тест G3"), active: await js(`document.activeElement && document.activeElement.getAttribute('data-did')`) };
}
// ── 40 человек: одно устройство — сразу окно выдачи; выключенный человек — переключатели устройств недоступны ──
{
  await open("&lang=ru&people=40");
  const r = {};
  r.groups = await js(`document.querySelectorAll('.gx-xt__g').length`);
  r.single = await js(`(() => { const b = [...document.querySelectorAll('.gx-xt__acts .ga-btn--xs.is-main[data-issue]')].find((x) => x.closest('.gx-xt__g > .gx-xt__r')); if (!b) return null; b.click(); return true; })()`);
  // QR → вкладки «Подписка (обновляется сама)» первой и «Статический ключ» (владелец 24.10, Т3 одно окно); статический — give.js
  r.pick = await until(`!!document.querySelector('[data-qr-pick="static"]')`, 6000).catch(() => false);
  r.pickOrder = await js(`[...document.querySelectorAll('[data-qr-pick]')].map((x) => x.getAttribute('data-qr-pick'))`);
  await js(`(() => { const b = document.querySelector('[data-qr-pick="static"]'); if (b) b.click(); return true; })()`);
  r.dialog = await until(`!!document.querySelector('.gs-dlg .gs')`, 6000).catch(() => false);
  await key("Escape", "Escape"); await sleep(400);
  r.offDevSw = await js(`(() => { const g = ${G("Егор")}; return [...g.querySelectorAll('.gx-xt__r--sub .gx-xt__sw')].map((b) => b.disabled); })()`);
  out.forty = r;
}
// ── стрелки по людям и раскрытым устройствам (З-1) ──
{
  await open("&lang=ru");
  const where = `(() => { const a = document.activeElement; if (!a) return null; const d = a.closest('.gx-xt__r--sub'); return d ? d.getAttribute('data-did') : (a.closest('.gx-xt__g') ? a.closest('.gx-xt__g').querySelector('.kk-pname').textContent : a.tagName); })()`;
  await js(`(${G("Мама")}).querySelector('.gx-xt__x').click(), true`);
  await js(`(${G("Мама")}).querySelector('.gx-xt__x').focus(), true`);
  const r = { down: [], up: [] };
  for (let i = 0; i < 4; i++) { await key("ArrowDown", "ArrowDown"); r.down.push(await js(where)); }
  for (let i = 0; i < 4; i++) { await key("ArrowUp", "ArrowUp"); r.up.push(await js(where)); }
  r.devs = await js(`[...(${G("Мама")}).querySelectorAll('.gx-xt__r--sub')].map((x) => x.getAttribute('data-did'))`);
  out.arrows = r;
}
// ── «⋯»: каждый пункт — то же действие (окно, переход, копирование), а не только подпись (З-8) ──
{
  const sig = async (rowSel, idx) => {
    await open("&lang=ru", "people", false);
    await js(`(() => { window.__clip = null; try { navigator.clipboard.writeText = (s) => { window.__clip = s; return Promise.resolve(); }; } catch (e) { /* нет */ } return true; })()`);
    if (rowSel.dev) { await js(`(() => { const g = ${G(rowSel.dev)}; if (!g.classList.contains('is-open')) g.querySelector('.gx-xt__x').click(); return true; })()`); await sleep(200); }
    const btn = rowSel.dev ? `(${G(rowSel.dev)}).querySelector('.gx-xt__r--sub .gx-menu-btn')` : `(${G(rowSel.user)}).querySelector('.gx-xt__r .gx-menu-btn')`;
    await js(`(${btn}).click(), true`);
    await until(`!!document.querySelector('#layers .gx-menu')`, 3000);
    const label = await js(`(() => { const b = document.querySelectorAll('#layers .gx-menu__item')[${idx}]; const l = b.querySelector('span:not(.gi):not(.gx-menu__k)').textContent; b.click(); return l; })()`);
    await sleep(700);
    const s = await js(`(() => { const d = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')].filter((x) => x.offsetParent !== null || x.getClientRects().length).pop();
      return { dlg: d ? (d.getAttribute('aria-label') || '') : null, hash: location.hash.split('?')[0], clip: window.__clip }; })()`);
    return label + " → " + (s.dlg !== null ? "окно: " + s.dlg : s.clip ? "скопировано: " + s.clip : "переход: " + s.hash);
  };
  const r = { person: [], device: [] };
  for (let i = 0; i < 10; i++) r.person.push(await sig({ user: "Мама" }, i));
  for (let i = 0; i < 9; i++) r.device.push(await sig({ dev: "Тест G3" }, i));
  out.actions = r;
}
// ── протоколы: порядок и места (С-1, владелец 20.10) ──
{
  await open("&lang=ru");
  const snap = `[...document.querySelectorAll('.gx-xt__g')].map((g) => g.querySelector('.kk-pname').textContent + ':' + [...g.querySelectorAll(':scope > .gx-xt__r .gx-xt__slots > [data-si]:not(.is-ph)')].map((c) => c.dataset.k + '@' + c.dataset.si).join(',')).join(' | ')`;
  const r = { slotsOf: await js(`[...document.querySelector('.gx-xt__g > .gx-xt__r .gx-xt__slots').querySelectorAll('[data-si]')].map((c) => c.dataset.k)`) };
  r.before = await js(snap);
  // порядок выдачи доступа — наоборот (в заглушке), перерисовка по событию state (переименование устройства)
  r.reversed = await js(`(async () => { const db = window.KASKAD_MOCK.db(); const devs = Array.isArray(db.devices) ? db.devices : Object.values(db.devices); let n = 0;
    devs.forEach((d) => { if (d.access && d.access.length > 1) { d.access.reverse(); n++; } });
    const d0 = devs[0]; await window.KASKAD_MOCK.fetch('/api/gw/devices/' + d0.id + '/name', { method: 'PUT', headers: { 'content-type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ name: d0.name + ' 2' }) }); return n; })()`);
  await until(`[...document.querySelectorAll('.gx-xt__n')].some((x) => / 2$/.test(x.textContent))`, 8000).catch(() => false);
  r.after = await js(snap);
  out.order = r;
  // 40 человек, все раскрыты: X одного протокола во всех строках, обрезанные плашки и «+N»
  // решение координатора 23.10 (окно от 1500): пустота внутри колонок — ширина колонки минус охват содержимого по всем
  // строкам (слева и справа); «Протоколы» — ширина колонки и сколько плашек видно в строке; линия «Активности» — во всю
  // ячейку, viewBox — её пиксели
  const WIDE = `(() => { const leaf = (e) => !e.children.length || e.matches('.gx-chip, button, .gx-xt__t, .gx-xt__n, .gx-xt__os, .gx-xt__more, .gx-dot, .gx-spark');
    const rows = [...document.querySelectorAll('.gx-xt__r:not(.gx-xt__r--add)')].filter((r) => r.getClientRects().length), col = {};
    rows.forEach((r) => [...r.children].forEach((c, i) => { if (!c.getClientRects().length) return; const cb = c.getBoundingClientRect(); let L = Infinity, R = -Infinity;
      c.querySelectorAll('*').forEach((e) => { if (!leaf(e) || !e.getClientRects().length || getComputedStyle(e).visibility === 'hidden' || e.matches('.is-ph, .is-cut, [hidden]')) return; const b = e.getBoundingClientRect(); if (!b.width) return; L = Math.min(L, b.left - cb.left); R = Math.max(R, Math.min(b.right, cb.right) - cb.left); });
      const x = col[i] = col[i] || { n: String(c.className).split(' ')[0], W: cb.width, L: Infinity, R: -Infinity }; x.L = Math.min(x.L, L); x.R = Math.max(x.R, R); }));
    const empty = Object.values(col).filter((x) => x.R > -Infinity).map((x) => [x.n, Math.round(x.W - (x.R - Math.min(x.L, 0)))]);
    const pc = document.querySelector('.gx-xt__r > .gx-xt__c > .gx-xt__slots'), sp = [...document.querySelectorAll('.gx-xt__r .gx-spark')].filter((e) => e.getClientRects().length);
    return { emptyMax: Math.max(...empty.map((x) => x[1])), empty, protoW: pc ? Math.round(pc.parentNode.getBoundingClientRect().width) : -1,
      shownMax: Math.max(0, ...[...document.querySelectorAll('.gx-xt__slots')].filter((b) => b.getClientRects().length).map((b) => b.querySelectorAll('[data-si]:not(.is-cut)').length)),
      sparkW: sp.length ? Math.round(Math.min(...sp.map((e) => e.getBoundingClientRect().width))) : 0,
      vbBad: sp.filter((e) => { const v = e.querySelector('svg'); return !v || Math.abs(Number(v.getAttribute('viewBox').split(' ')[2]) - e.getBoundingClientRect().width) > 1; }).length }; })()`;
  for (const [w, dn] of [[1440, "md"], [2000, "md"], [2560, "md"], [1440, "lg"], [1180, "md"], [1500, "md"], [2000, "lg"], [2560, "lg"]]) {
    // тело той же ширины, что у UI-B без подменю (UI-A1: окно шире на подменю — BODY)
    await cdp("Emulation.setDeviceMetricsOverride", { width: BODY(w), height: 900, deviceScaleFactor: 1, mobile: false });
    await open("&lang=ru&people=40");
    await js(`(localStorage.setItem('kaskad-density-people', '${dn}'), true)`); await cdp("Page.reload"); await until(`!!document.querySelector('.gx-xt__g')`, 20000); await sleep(500);
    await js(`(document.querySelectorAll('.gx-xt__x[aria-expanded=false]').forEach((b) => b.click()), true)`); await sleep(500);
    out.order["w" + w + dn] = await js(`(() => { const xs = {}; let cut = 0, chips = 0;
      document.querySelectorAll('.gx-xt__slots > [data-si]:not(.is-cut)').forEach((c) => { if (!c.getClientRects().length) return; chips++; if (c.scrollWidth > c.clientWidth + 1) cut++; (xs[c.dataset.k] = xs[c.dataset.k] || []).push(c.getBoundingClientRect().left); });
      const ord = {}, orderBad = []; document.querySelectorAll('.gx-xt__slots').forEach((b) => { const q = [...b.querySelectorAll('[data-k]')].map((c) => c.dataset.k); q.forEach((k, i) => q.slice(i + 1).forEach((k2) => { if (ord[k2 + '<' + k]) orderBad.push(k + '/' + k2); ord[k + '<' + k2] = 1; })); });
      // имена пользователей не обрезаны нигде; имя устройства — только в узкой колонке «Средне» (176, HANDOFF «Сетки gx-xt--act»)
      // и тогда целиком в подсказке
      const cutN = [...document.querySelectorAll('.gx-xt__r .gx-xt__n')].filter((e) => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1);
      const narrowDev = (e) => !!e.closest('.gx-xt__r--sub') && e.closest('.gx-xt__x').getBoundingClientRect().width < 200;
      // Д2 Б: имя постоянной ширины — длинное многоточием, но только с полным именем в подсказке
      const names = cutN.filter((e) => !narrowDev(e) && (e.getAttribute('title') || '').indexOf(e.textContent) !== 0).length;
      const devCutNoTip = cutN.filter((e) => narrowDev(e) && (e.getAttribute('title') || '').indexOf(e.textContent) !== 0).length;
      const boxes = [...document.querySelectorAll('.gx-xt__slots')].filter((b) => b.getClientRects().length);
      const sp = (sel) => { const v = [...document.querySelectorAll(sel)].filter((e) => e.getClientRects().length).map((e) => e.getBoundingClientRect().left); return v.length ? Math.round(Math.max(...v) - Math.min(...v)) : -1; };
      // колонка «Состояние» и «В сети» — одна X во всех строках; между именем и «Состоянием» / «В сети» нет «резиновой» пустоты
      const r0 = document.querySelector('.gx-xt__g > .gx-xt__r'), tb = document.querySelector('.gx-xt--k1').getBoundingClientRect();
      const nameR = Math.max(...[...document.querySelectorAll('.gx-xt__r .gx-xt__n, .gx-xt__r .gx-xt__os')].filter((e) => e.getClientRects().length).map((e) => Math.min(e.getBoundingClientRect().left + e.scrollWidth, e.getBoundingClientRect().right)));
      // Д2 Б: колонки «Состояние» нет — от имени до «Активности» (первая колонка после имени) без «резиновой» пустоты
      const stL = Math.min(...[...document.querySelectorAll('.gx-xt__r > .kk-act1')].filter((e) => e.getClientRects().length).map((e) => e.getBoundingClientRect().left));
      return { slots: Number(document.querySelector('.gx-xt--k1').dataset.slots), chips, cut, over: boxes.filter((b) => b.scrollWidth > b.clientWidth + 1).length,
        orderBad: [...new Set(orderBad)], names, devCutNoTip, xState: sp('.gx-xt__r > .kk-act1'), xOnline: sp('.gx-xt__r > .kk-on2 > .kk-on2__d'), nameToState: Math.round(stL - nameR), tableW: Math.round(tb.width), barW: Math.round(document.querySelector('.kk-pp__bar').getBoundingClientRect().width), tableL: Math.round(tb.left - document.querySelector('.kk-pp__bar').getBoundingClientRect().left),
        spread: Math.round(Math.max(0, ...Object.values(xs).map((a) => Math.max(...a) - Math.min(...a))) * 10) / 10, more: document.querySelectorAll('.gx-xt__more:not([hidden])').length }; })()`);
    out.order["w" + w + dn].wide = await js(WIDE);
  }
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
}
// ── числа фильтров: в сети + не в сети + выключены = все; число = строк людей в списке ──
{
  await open("&lang=ru&people=40");
  // nav2 r1: фильтры — пункты-кнопки подменю слева (ряда gx-seg над списком нет), адрес — #people?show=…
  const btn = (re) => `[...document.querySelectorAll('nav[data-subnav="people"] button.gx-subnav__i')].find((b) => ${re}.test(b.querySelector('.gx-subnav__l').textContent.trim()))`;
  const num = (re) => js(`Number((${btn(re)}).querySelector('.gx-subnav__n').textContent.replace(/\\D+/g, ''))`);
  const c = { all: await num("/^Все/"), online: await num("/^В сети/"), offline: await num("/^Не в сети/"), off: await num("/^На паузе/"), rows: {}, addr: {}, head: {},
    seg: await js(`[...document.querySelectorAll('.kk-pp__bar .gx-seg')].filter((s) => /В сети|На паузе/.test(s.textContent)).length`) };   // переключатель плотности — не фильтр
  for (const [k, re] of [["online", "/^В сети/"], ["offline", "/^Не в сети/"], ["off", "/^На паузе/"], ["all", "/^Все/"]]) {
    await js(`(${btn(re)}).click(), true`); await sleep(400);
    c.rows[k] = await js(`document.querySelectorAll('.gx-xt__g').length`);
    c.addr[k] = await js(`location.hash`);
    c.head[k] = await js(`[(document.querySelector('#head .gx-crumb--path') || {}).textContent || '', document.querySelector('#head h1').textContent]`);
  }
  out.counts = c;
}
// ── «Строка»: колонка человека не меняется при раскрытии; состояние устройства — точкой ──
{
  await cdp("Emulation.setDeviceMetricsOverride", { width: BODY(1440), height: 900, deviceScaleFactor: 1, mobile: false });
  await open("&lang=ru&people=40");
  await js(`document.querySelector('.kk-pp__dn [data-dn=row]').click(), true`); await sleep(300);
  const cols = `Object.fromEntries([...document.querySelectorAll('.gx-xt__g')].map((g) => [g.querySelector('.kk-pname').textContent, Math.round(g.querySelector('.gx-xt__r').getBoundingClientRect().left)]))`;
  const r = { tpl: await js(`getComputedStyle(document.querySelector('.gx-xt__cols')).gridTemplateColumns.split(' ').length`), before: await js(cols) };
  // эталон 23.10 (А3) — колонками: первая половина людей — в левой колонке сверху вниз, вторая — в правой
  r.colMajor = await js(`(() => { const g = [...document.querySelectorAll('.gx-xt__g')], h = Math.ceil(g.length / 2), x0 = g[0].querySelector('.gx-xt__r').getBoundingClientRect().left;
    return g.every((n, i) => (Math.abs(n.querySelector('.gx-xt__r').getBoundingClientRect().left - x0) < 2) === (i < h)); })()`);
  await js(`(${G("Бабушка")}).querySelector('.gx-xt__x').click(), true`); await sleep(300);
  r.after = await js(cols);
  r.moved = Object.keys(r.before).filter((k) => r.before[k] !== r.after[k]);
  // устройства — под своим человеком, в его колонке
  r.subInCol = await js(`(() => { const g = ${G("Бабушка")}, s = g.querySelector('.gx-xt__sub').getBoundingClientRect(), c = g.closest('.gx-xt__col').getBoundingClientRect(); return Math.abs(s.width - c.width) < 2 && Math.abs(s.left - c.left) < 2; })()`);
  r.dot = await js(`[...(${G("Бабушка")}).querySelectorAll('.gx-xt__r--sub .gx-xt__c.kk-on2')].map((c) => [c.title, c.querySelector('.gx-xt__t') ? getComputedStyle(c.querySelector('.gx-xt__t')).display : 'none'])`);
  r.cut = await js(`[...document.querySelectorAll('.gx-xt__r .gx-xt__c, .gx-xt__r .gx-xt__t')].filter((e) => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1 && !e.closest('.kk-act1') && !e.closest('[title]')).length`);
  await js(`(localStorage.removeItem('kaskad-density-people'), true)`);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  out.rowgrid = r;
}
// ── фильтр на планшете и телефоне (З-4) ──
{
  await open("&lang=ru");
  await js(`[...document.querySelectorAll('nav[data-subnav="people"] button.gx-subnav__i')].find((b) => /^Не в сети/.test(b.textContent.trim())).click(), true`); await sleep(400);
  const r = { addr: await js(`location.hash`) };
  const seg = `[...document.querySelectorAll('section[data-page="people"] .gx-seg button')].filter((b) => b.getClientRects().length).map((b) => b.textContent.trim().replace(/\s*\d+$/, '') + ':' + (b.getAttribute('aria-pressed') || b.getAttribute('aria-checked') || b.getAttribute('aria-selected') || b.classList.contains('is-on')))`;
  // телефон (nav2 r1, Т1): вместо ряда фильтров — кнопка «Показать · фильтр · N ▾» и лист
  const pick = `(document.querySelector('section[data-page="people"] .gx-partpick') || {}).textContent || ''`;
  for (const w of [1024, 390]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: w < 600 }); await sleep(900);
    const x = { seg: await js(seg), pick: await js(pick) };
    x.names = await js(`[...document.querySelectorAll('section[data-page="people"] .kk-prow__n')].map((n) => n.textContent)`);
    out["narrow" + w] = x;
  }
  await js(`(async () => { document.querySelector('section[data-page="people"] .gx-partpick').click(); await new Promise((ok) => setTimeout(ok, 400)); [...document.querySelectorAll('.gx-bsheet .gx-mrow')].find((b) => /^Все/.test(b.textContent.trim())).click(); return true; })()`); await sleep(600);
  r.afterAll = await js(pick);
  r.addrAll = await js(`location.hash`);
  // UI-A1 × UI-B: к человеку в сети адресом с фильтром «В сети» (телефон) — лист человека, в адресе остаётся show:
  // шапка и подменю читают фильтр из адреса
  r.focusUid = await js(`(document.querySelector('section[data-page="people"] .kk-prow:has(.kk-on2__d .gx-dot--on)') || { dataset: {} }).dataset.uid || ''`);
  if (r.focusUid) {
    await js(`(location.hash = '#people?focus=' + ${JSON.stringify(r.focusUid)} + '&show=on', true)`);
    r.focusSheet = await until(`!!document.querySelector('.gx-bsheet--act')`, 8000).catch(() => false);
    await sleep(300); r.focusAddr = await js(`location.hash`); r.focusPick = await js(pick);
    await key("Escape"); await sleep(500);
  }
  out.narrow = r;
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
}
// ── REV-3377-ui №1: компьютер — человек из адреса под фильтром, смена фильтра, окно по адресу ──
{
  const toasts = `[...document.querySelectorAll('#toastHost *')].map((n) => n.textContent).filter((x) => /уже нет/.test(x)).length`;
  await open("&lang=ru&people=40", "people?show=off");
  const r = { uid: await js(`(document.querySelector('.gx-xt__g[data-uid]') || { dataset: {} }).dataset.uid || ''`) };
  // (а) человек не в сети, а в адресе фильтр «В сети»: без «уже нет», фильтр — «Все», фокус на его строке
  await open("&lang=ru&people=40", "people?focus=" + r.uid + "&show=on", false);   // без перезагрузки: метка перехода — в адресе первой загрузки
  const gone0 = await js(toasts);                                   // тост живёт секунды — читать сразу
  await until(`document.activeElement && !!document.activeElement.closest('.gx-xt__g[data-uid="${r.uid}"]')`, 8000).catch(() => false);
  r.hidden = { gone: gone0 + await js(toasts), addr: await js(`location.hash`), focused: await js(`!!(document.activeElement && document.activeElement.closest('.gx-xt__g[data-uid="${r.uid}"]'))`),
    cur: await js(`(document.querySelector('nav[data-subnav="people"] [aria-current="page"]') || {}).textContent || ''`) };
  // (б) пришли к человеку (focus), затем «В сети» в подменю: метки focus в адресе больше нет
  await open("&lang=ru&people=40", "people?focus=" + r.uid, false);   // без перезагрузки: метка перехода — в адресе первой загрузки
  await js(`[...document.querySelectorAll('nav[data-subnav="people"] button.gx-subnav__i')].find((b) => /^В сети/.test(b.textContent.trim())).click(), true`); await sleep(400);
  r.pick = { addr: await js(`location.hash`) };
  // (в) окно маршрута по адресу с фильтром «Не в сети»: после открытия в адресе focus и show=off
  await open("&lang=ru&people=40", "people?show=off&focus=" + r.uid + "&open=route", false);   // без перезагрузки: метка перехода — в адресе первой загрузки
  r.open = { dlg: await until(`!!document.querySelector('[role=dialog]')`, 8000).catch(() => false) };
  await sleep(300); r.open.addr = await js(`location.hash`); r.open.gone = await js(toasts);
  await key("Escape"); await sleep(400);
  out.navfocus = r;
}
// ── REV-3377-ui №2: настоящее окно 1100 / 1280 / 1366 (без BODY), «Средне»: на 1100 без «Маршрута», «Протоколы» —
// заголовок целиком и в каждой строке с доступом видна плашка с названием; на 1280 и 1366 «Маршрут» есть ──
{
  out.narrowMd = {};
  for (const w of [1100, 1280, 1366]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: false });
    await open("&lang=ru");
    out.narrowMd[w] = await js(`(() => { const hd = [...document.querySelector('.gx-xt__h').children], lb = (h) => (h.querySelector('.gx-xt__hl') || h).textContent;
      const ph0 = hd.find((h) => lb(h) === 'Протоколы'), ph = ph0 && (ph0.querySelector('.gx-xt__hl') || ph0);
      const rt0 = hd.find((h) => lb(h) === 'Маршрут'), rt = rt0 && !rt0.classList.contains('is-cc') ? rt0 : null, vis = (e) => !!e && e.getClientRects().length > 0 && e.getBoundingClientRect().width > 0;
      const rows = [...document.querySelectorAll('.gx-xt__list .gx-xt__r')].filter((r) => vis(r) && r.querySelector('.gx-xt__slots [data-si]'));
      const mut = [...document.querySelectorAll('.gx-xt__list .gx-xt__tags .gx-xt__mut')].filter(vis);
      return { dn: document.querySelector('.kk-pp__body').className, tableW: Math.round(document.querySelector('.gx-xt').getBoundingClientRect().width),
        protoCut: !ph || (!ph0.classList.contains('is-cc') && ph.scrollWidth > ph.clientWidth + 1), ccProto: !!ph0 && ph0.classList.contains('is-cc'), route: vis(rt), rows: rows.length,
        noChip: rows.filter((r) => ![...r.querySelectorAll('.gx-xt__slots > [data-si]:not(.is-cut)')].some((c) => vis(c) && c.textContent.trim())).length,
        mutCut: mut.filter((e) => e.scrollWidth > e.clientWidth + 1).length }; })()`);
  }
  // Т3 №23: на 1100 «Маршрут» свёрнут сам — нажатие разворачивает его до перезагрузки, выбор в браузере не меняется;
  // на 1440 «Активность» сворачивается нажатием, выбор помнится (kaskad-cols-people) и после перезагрузки, второе нажатие — обратно
  const ccSt = `(() => { const t = document.querySelector('.gx-xt--k1'), on = (k) => { const h = t.querySelector('.gx-xt__h [data-ck=' + k + ']'); return !!h && h.classList.contains('is-cc'); };
    return { act: on('act'), proto: on('proto'), route: on('route'), ls: localStorage.getItem('kaskad-cols-people') }; })()`;
  const ccHit = (k) => js(`(document.querySelector('.gx-xt--k1 .gx-xt__h [data-ck=${k}]').click(), true)`);
  out.ccClick = {};
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1100, height: 900, deviceScaleFactor: 1, mobile: false });
  await open("&lang=ru"); await js(`(localStorage.removeItem('kaskad-cols-people'), true)`); await open("&lang=ru"); await sleep(300);
  out.ccClick.auto0 = await js(ccSt); await ccHit("route"); await sleep(300); out.ccClick.auto1 = await js(ccSt);
  // T-it1b Н-2: развернуть вручную и «Протоколы» — сворачивать нечего; линия «Активность» не уже --cc-min (120),
  // место уступают имя и «Маршрут», строка не шире таблицы
  if (out.ccClick.auto1.proto) { await ccHit("proto"); await sleep(400); }
  out.ccClick.tight = await js(`(() => { const t = document.querySelector('.gx-xt--k1'), tb = t.getBoundingClientRect(), r = t.querySelector('.gx-xt__list .gx-xt__r');
    const w = (k) => { const c = r.querySelector('[data-ck=' + k + ']'); return c ? Math.round(c.getBoundingClientRect().width) : null; };
    return { tight: t.classList.contains('is-cc-tight'), act: w('act'), proto: w('proto'), route: w('route'), over: Math.round(Math.max(...[...r.children].map((c) => c.getBoundingClientRect().right)) - tb.right) }; })()`);
  // Доводка круга 1 (T-it1b, придирка к Н-2): там же, но на каждом шаге «A A A», в «Крупно» и по-английски — имя не
  // уже первого слова, видимые плашки протоколов целиком (без обрезки посреди слова), сжимается «Маршрут», строка в таблице
  const tightRows = `(() => { const t = document.querySelector('.gx-xt--k1'), tb = t.getBoundingClientRect(), cv = document.createElement('canvas').getContext('2d');
    const rows = [...t.querySelectorAll('.gx-xt__list .gx-xt__g > .gx-xt__r')];
    return { tight: t.classList.contains('is-cc-tight'), rows: rows.map((r) => { const n = r.querySelector('.kk-pname'), cs = getComputedStyle(n), w = (k) => { const c = r.querySelector('[data-ck=' + k + ']'); return c && c.getClientRects().length ? Math.round(c.getBoundingClientRect().width) : null; };
      cv.font = cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
      const chips = [...r.querySelectorAll('.gx-xt__slots > [data-si]:not(.is-cut)')].filter((c) => c.getClientRects().length);
      return { name: n.textContent, word1: n.clientWidth + 1 >= cv.measureText(n.textContent.trim().split(/\\s+/)[0]).width,
        chipCut: chips.filter((c) => c.getBoundingClientRect().height > 24 || (c.scrollWidth > c.clientWidth + 1 && getComputedStyle(c).textOverflow !== 'ellipsis')).map((c) => c.textContent),
        vis: chips.length, access: !!r.querySelector('.gx-xt__slots [data-si]'), slots: !!r.querySelector('.gx-xt__slots') && r.querySelector('.gx-xt__slots').getClientRects().length > 0,
        proto: w('proto'), route: w('route'), act: w('act'), over: Math.round(Math.max(...[...r.children].map((c) => c.getBoundingClientRect().right)) - tb.right) }; }) }; })()`;
  out.ccClick.steps = {};
  for (const [lang, fs, dn] of [["ru", "s", "md"], ["ru", "m", "md"], ["ru", "l", "md"], ["ru", "m", "lg"], ["ru", "l", "lg"], ["en", "m", "md"], ["ru", "m", "row"], ["ru", "l", "row"]]) {
    await open("&lang=" + lang); await js(`(localStorage.removeItem('kaskad-cols-people'), localStorage.setItem('kaskad-font', '${fs}'), true)`);
    await open("&lang=" + lang, "people", false);
    if (dn !== "md") { await js(`(document.querySelector('.kk-pp__dn [data-dn=${dn}]').click(), true)`); await sleep(300); }
    for (const k of ["proto", "route"]) {
      if (await js(`(() => { const h = document.querySelector('.gx-xt--k1 .gx-xt__h [data-ck=${k}]'); return !!h && h.getClientRects().length > 0 && h.classList.contains('is-cc'); })()`)) { await ccHit(k); await sleep(350); }
    }
    out.ccClick.steps[lang + "-" + fs + "-" + dn] = await js(tightRows);
  }
  await js(`(localStorage.removeItem('kaskad-font'), localStorage.removeItem('kaskad-density-people'), localStorage.removeItem('kaskad-cols-people'), true)`);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
  await open("&lang=ru"); await sleep(300);
  out.ccClick.wide0 = await js(ccSt); await ccHit("act"); await sleep(300); out.ccClick.wide1 = await js(ccSt);
  await open("&lang=ru"); await sleep(300); out.ccClick.wide2 = await js(ccSt);
  await ccHit("act"); await sleep(300); out.ccClick.wide3 = await js(ccSt);
  await js(`(localStorage.removeItem('kaskad-cols-people'), true)`);
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
}
// ── Т3 №17: «A A A» в шапке — html { font-size } 112,5 %, весь текст в rem (имя 16 → 18), выбор — только в браузере
//    (kaskad-font; «Вид» узла не меняется), после перезагрузки тот же; стрелка влево — шаг назад ──
{
  await open("&lang=ru"); await js(`(localStorage.removeItem('kaskad-font'), true)`); await open("&lang=ru");
  const fz = `(() => { const h = document.documentElement, n = document.querySelector('.kk-pname');
    return { fs: h.dataset.fs, html: getComputedStyle(h).fontSize, name: n && getComputedStyle(n).fontSize, ls: localStorage.getItem('kaskad-font'), look: localStorage.getItem('kaskad-appearance-v1'),
      on: [...document.querySelectorAll('.kb2-fz [data-fz]')].map((b) => b.dataset.fz + ':' + b.getAttribute('aria-checked')).join(' '), role: (document.querySelector('.kb2-fz') || {}).getAttribute && document.querySelector('.kb2-fz').getAttribute('role'),
      kbd: !!document.querySelector('.kk-hfind__k') && getComputedStyle(document.querySelector('.kk-hfind__k')).display !== 'none', ph: (document.querySelector('.kk-hfind__t') || {}).textContent }; })()`;
  out.font = { m0: await js(fz) };
  await js(`(document.querySelector('.kb2-fz [data-fz=l]').click(), true)`); await sleep(500);
  out.font.l1 = await js(fz);
  await open("&lang=ru", "people", false); await sleep(300);
  out.font.l2 = await js(fz);
  await js(`(document.querySelector('.kb2-fz [data-fz=l]').focus(), true)`); await key("ArrowLeft", "ArrowLeft"); await sleep(500);
  out.font.m3 = await js(fz); out.font.focus = await js(`document.activeElement && document.activeElement.dataset.fz`);
  await js(`(localStorage.removeItem('kaskad-font'), true)`);
}
// ── подписи для диктора, причина недоступного, «устройства выключены» ──
{
  await open("&lang=ru&people=40");
  // такт сборщика заглушки, в котором без still=1 «в сети» переключилось бы у каждого устройства (Math.random — 0):
  // подпись строки ниже ждёт точное «в сети 2 из 2»
  out.ariaTick = await js(`new Promise((ok) => { const db = window.KASKAD_MOCK.db(), R = Math.random; Math.random = () => 0; db._simAt = 0;
    const t = setInterval(() => { if (db._simAt) { clearInterval(t); Math.random = R; setTimeout(() => ok(true), 800); } }, 50); })`);
  await js(`(${G("Егор")}).querySelector('.gx-xt__x').click(), true`); await sleep(200);
  out.aria = await js(`(() => { const g = ${G("Мама")} || ${G("Анна")}; const r = g.querySelector('.gx-xt__r');
    const e = ${G("Егор")}, d = ${G("Дарья")};
    return { row: r.getAttribute('aria-label'), give: r.querySelector('.gx-xt__acts .ga-btn--xs.is-main').getAttribute('aria-label'), more: r.querySelector('.gx-menu-btn').getAttribute('aria-label'),
      dev: e.querySelector('.gx-xt__r--sub').getAttribute('aria-label'), devSw: e.querySelector('.gx-xt__r--sub .gx-xt__sw').title,
      darya: [d.querySelector('.gx-xt__nl > .gx-chip') && d.querySelector('.gx-xt__nl > .gx-chip').textContent, d.querySelector('.gx-xt__r').classList.contains('is-off')] }; })()`);
}
// ── длинное имя: метка проблемы целиком, обрезается имя (З-6) ──
{
  await open("&lang=ru&people=40");
  await js(`(async () => { const db = window.KASKAD_MOCK.db(); const us = Array.isArray(db.users) ? db.users : Object.values(db.users); const u = us.find((x) => x.name === "Борис 2");
    await window.KASKAD_MOCK.fetch('/api/gw/users/' + u.id + '/name', { method: 'PUT', headers: { 'content-type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ name: ("Борис " + "Очень-длинное-имя-".repeat(2)).slice(0, 40) }) }); return true; })()`);
  await until(`[...document.querySelectorAll('.kk-pname')].some((x) => /Очень/.test(x.textContent))`, 8000);
  await sleep(300);
  out.longname = await js(`(() => { const g = [...document.querySelectorAll('.gx-xt__g')].find((x) => /Очень/.test(x.querySelector('.kk-pname').textContent)); const c = g.querySelector('.gx-xt__nl > .gx-chip'), n = g.querySelector('.kk-pname');
    return { chip: c.textContent, chipFull: c.scrollWidth <= c.clientWidth + 1 && c.getBoundingClientRect().width > 40, nameCut: n.scrollWidth > n.clientWidth, nameW: Math.round(n.getBoundingClientRect().width) }; })()`);
}
// ── пустой узел ──
{
  await open("&lang=ru&none=people");
  out.empty = await js(`({ empty: !!document.querySelector('.gx-xt .gx-xt__empty'), barHidden: document.querySelector('.kk-pp__bar').hidden,
    btn: [...document.querySelectorAll('.gx-xt__empty button')].map((b) => b.textContent.trim()) })`);
}
// ── EN ──
{
  await open("&lang=en");
  out.en = await js(`({ head: [...document.querySelector('.gx-xt__h').children].map((s) => (s.querySelector('.gx-xt__hl') || s).textContent).filter(Boolean), dn: [...document.querySelectorAll('.kk-pp__dn [role=radio]')].map((b) => b.textContent),
    since: [...document.querySelectorAll('.gx-xt__r > .kk-on2[title]')].map((s) => s.title).filter((x) => /since/.test(x)) })`);
  // О5 по-английски: шапка, платформы, приложения, подвал
  await js(`(() => { (${G("Mom")}).querySelector('.gx-xt__acts .ga-btn--xs.is-main').click(); return true; })()`);
  await until(`!!document.querySelector('.kk-iss .kk-iss__plats > [role=radio]')`, 6000).catch(() => false); await sleep(400);
  out.giveEn = await js(`(() => { const d = document.querySelector('.kk-iss'); return { ...${GHEAD}, proto: d.querySelector('.kk-iss__pick > .kk-iss__all').textContent }; })()`);
  await key("Escape", "Escape"); await sleep(300);
  // сцена узла без проблем: колонки «Состояние» нет (ширина 0)
  await open("&lang=ru&scene=node3373");
  out.nost = await js(`({ nost: !document.querySelector('.gx-xt__st'), w: document.querySelector('.gx-xt--k1').classList.contains('kk-pp-mk') ? 1 : 0, chips: document.querySelectorAll('.gx-xt__nl > .gx-chip').length })`);
}
console.log(JSON.stringify(out));
stop();
process.exit(0);
