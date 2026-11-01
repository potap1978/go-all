// k3_hold_check.mjs — открытое на месте действие переживает живую перерисовку панели (3.3.5, стенд T-335-release П2):
// мок (?mock=1) шлёт живые данные раз в 2 с, плитки «Выходов» и карточки «Протоколов» перерисовываются по ним. Для
// каждого места:
//   before  — сколько раз список перерисовался за 4 с до открытия (0 — проверка ничего не доказала бы);
//   kept    — через 5 с (две перерисовки) подтверждение / меню на месте, тот же узел, что открыли (не собранный заново);
//   after   — после «Отмена» (Esc) список снова перерисовывается по данным (удержание отпущено).
// Места: exits-confirm (корзина плитки выхода, U.askDelete), people-menu (меню «⋯» строки человека, таблица К1 UI-4),
// inbounds-confirm (ProtocolCard ux.js, строка «Протоколов» Б r3: «Удалить протокол» → подтверждение на месте),
// inbounds-ask (там же: «Перезапустить» упавшего VLESS → вопрос в строке; до Т3 — открытое «⋯» карточки, «⋯» больше нет);
// inbounds-more (UI-1 З-3): раскрытая строка VLESS — после пересборки (rebuilt) снова раскрыта (kept), только она
// (others = 1); свёрнутая — после пересборки свёрнута (again, closed);
// exits-delete — подтверждение пережило перерисовки, «Удалить» удалило, плитки нет (kept, gone).
// REV-335-5 №1 — поле в фокусе держит карточку, только пока ввод не ушёл:
//   site-enter — «Имя домена» → vpn.example.com → Enter: карточка «Домен и сайт» собралась сама, без щелчка мимо
//                («Убрать домен» на месте — updated; прежнего поля нет — rebuilt);
//   gw-typed   — «Адрес для клиентов»: напечатанное, пока данные шлюза сменились, на месте (kept); Enter — карточка
//                собралась по сохранённому (sent).
// REV-335-5 №2 — подтверждение держит только свою плитку и не висит:
//   exits-two  — корзина у первой плитки, затем щелчком мыши у последней: первое закрылось (firstClosed), второе
//                открыто (secondOpen); через 5 с остальные плитки собраны заново (othersLive), второе на месте
//                (secondKept); «Удалить» — плитки нет (gone);
//   toast-over — тост поставлен на корзину последней строки (covered): помощник нажатия убрал его (cleared), щелчок
//                открыл её подтверждение (opened) — R-3377-hold, тост живого сообщения мока над корзиной забирал нажатие;
//   exits-away — щелчок мимо закрывает подтверждение (clickClosed), фокус вне ряда — тоже (focusClosed);
//   exits-idle — через 5 с ещё открыто (kept), через 31 с без действия закрылось само (closed), фокус — на корзине
//                (focusBack, REV-335-6 Н2); в exits-two фокус после двух перерисовок — на «Отмена» (focusKept, Н3).
// Стенд П8 — exits-warn-below: открыто подтверждение со строкой предупреждения у верхней плитки, мышью нажата корзина
//   плитки ниже: первое закрылось (firstClosed), второе открылось (secondOpen) с первого щелчка.
// REV-335-6 Н1 — gw-other-card: «Сохранить» домена, пока идёт запрос, фокус в несохранённом поле «Адрес для клиентов»;
//   данные шлюза сменились — напечатанное на месте (kept).
// REV-335-6 Н3, Н2 — ProtocolCard (ux.js): inbounds-two — второе подтверждение закрывает первое (one), щелчок мимо
//   закрывает (clickClosed); inbounds-idle — через 31 с закрылось само (closed), фокус — на «Удалить протокол» своей
//   строки (focusBack; до Т3 — на «⋯»).
// 3.3.6 — REV-335-7, REV-335-6 Н4/Н5, BACKLOG-3.3.6 п. 8:
//   exits-touch-below — то же, что exits-warn-below, касанием на телефоне (390, Input.dispatchTouchEvent: pointerup
//                       раньше mousedown и ухода фокуса, Т1);
//   exits-long-press  — нажатие на корзину плитки ниже держат 2,6 с (дольше перерисовки): список под нажатием не
//                       перерисован (kept), страница не прыгнула (still), щелчок не потерян (П2);
//   exits-right       — правый щелчок по чужой корзине закрывает подтверждение сразу (П3);
//   exits-focus       — Esc: фокус на корзине своей плитки (escBack, стенд П6); выход удалён в другом месте при открытом
//                       подтверждении — фокус на заголовке раздела (goneFocus, Н5);
//   exits-sub-menu    — открытое «⋯» подписки в ExitsInput держит только свой ряд (REV-335-5 №5);
//   inbounds-press    — ProtocolCard: нажатие на «Удалить протокол» другой строки не закрывает, щелчок закрывает (П4,
//                       условие ux.js);
//   inbounds-restart  — VLESS упал (uxfail), «Перезапустить» общей службы → вопрос; «Отмена» и Esc — фокус на
//                       «Перезапустить» живой карточки (П5);
//   gw-typed.focusKept, site-enter.focusNotBody — после Enter фокус не в body (Н4).
// REV-336 З-3 и С-1:
//   exits-touch-hold — касание, click ещё не пришёл, кадр данных: плитка под пальцем ждёт (kept), потом собрана (rebuilt);
//   exits-right-live — правая кнопка зажата на корзине, кадр данных: плитка собрана (live — правая кнопка не держит);
//   gw-typed.nextShown — после Enter новые данные шлюза видны в поле, где стоит фокус (поле помечено отправленным);
//   flash            — подсветка is-flash видна (вычисленная box-shadow не none): тёмная/светлая, карточки border/flat;
//                      поиск (?focus=settings.bot), человек (#people?focus=u-…), адрес шлюза (?open=host).
// REV-336-2 З-1, П-4:
//   flash-view       — 390 и 1440×720: после перехода (поиск, ?open=bot|mcp, человек) цель на экране, когда раздел
//                      дособран; у человека — его имя; на 1440 «Бот» и «ИИ-агент» — у верха области.
// REV-336-mobile С-1:
//   settings-back    — 390: ?open=bot|mcp|host и ?focus=settings.* — адрес становится ?tab=<часть>, «‹ Настройки» ведёт
//                      к списку частей.
//   share-copy       — 390: «Поделиться» — адреса при открытии листа, копирование в нажатии; буфер отказал — адрес полем.
//   sheet-swipe      — 390: высоты строк 52/60 по макету; лист смахивается касанием вниз (З-1, З-3).
//   phone-ux         — 390/768: «Требует внимания» двумя строками и с переходом, плитка «Шлюз», устройства без фильтра,
//                      подпись строки с состоянием, одна акцентная кнопка у человека (З-4, З-5, З-6, З-8, П-2).
//   phone-forms      — 390: форма во весь экран над вкладками (З-7), вкладки под ней притушены (REV-336-mobile-2 З-11);
//                      протокол (Т3, Б r3 Т2): строка раскрывается на месте — кнопки в строке, без «⋯» и листа (было
//                      З-9: лист «⋯»), касание ≥ 44 px, переключатели протокола — role="switch" (П-11).
// REV-336-mobile-2:
//   sheet-low        — 390×360 и 390×300: лист «⋯» человека — последний пункт виден или тело листа прокручивается (З-10);
//   live-label       — строка проброса на телефоне: живая вторая строка — описанием (aria-describedby), не в подписи (П-10).
// node tools/k3_hold_check.mjs <адрес статики панели> <chrome> [1|2|all]  → JSON в stdout (часть: 1 — до inbounds-idle,
// 2 — с exits-touch-below; без неё — все места)
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
// REV-336 П-1: порт отладки выбирает сам Chrome (0 → свободный) и пишет его в DevToolsActivePort профиля, как в
// k3_layout_check.mjs. Случайный порт из 190 при параллельных прогонах совпадал, и сценарий водил страницу чужого Chrome.
const prof = mkdtempSync(join(tmpdir(), "k3hold-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) {} } try { rmSync(prof, { recursive: true, force: true }); } catch (e) {} };
process.on("exit", stop);
// REV-336-int П-3: прогон целиком — около 350 с, и почти всё — ожидание (живые кадры мока раз в 2 с, 30 с простоя
// подтверждения), а не работа. Поэтому места разбиты на две части почти поровну (~175 с) — tests/test_335_hold.py
// запускает их одновременно, каждую в своём Chrome; проверки те же. Предел — 400 с на часть (запас вдвое под -j4) и
// 700 с на прогон целиком (без части — все места подряд, как раньше).
const PART = process.argv[4] || "all";              // "1", "2" или "all"
if (!["1", "2", "all"].includes(PART)) { console.error("k3_hold_check: часть — 1, 2 или all, а не " + PART); process.exit(2); }
setTimeout(() => { console.error("k3_hold_check: время вышло (часть " + PART + ")"); process.exit(3); }, PART === "all" ? 700000 : 400000).unref();
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
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { if (await js(expr)) return true; await sleep(150); } throw new Error("не дождался: " + expr); }
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

// Счётчик перерисовок: сколько раз у узлов под ``sel`` (блок списка) сменились дети. window.__rb — число.
const WATCH = (sel) => `(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) return false; if (window.__mo) window.__mo.disconnect();
  window.__rb = 0; window.__mo = new MutationObserver((l) => { if (l.some((m) => m.type === "childList" && (m.addedNodes.length || m.removedNodes.length))) window.__rb++; });
  window.__mo.observe(n, { childList: true, subtree: true }); return true; })()`;
const RB = `window.__rb`;
const ZERO = `(window.__rb = 0, true)`;

// Что на странице, если раздел не собрался (REV-336-int П-3: «не дождался» под нагрузкой — без причины): адрес, ширина,
// видимый раздел, заготовки и ошибки, файлы, которые не загрузились
const PAGE_DIAG = `(() => { const p = document.querySelector('.gx-page:not([hidden])');
  const bad = performance.getEntriesByType('resource').filter((r) => (r.responseStatus || 0) >= 400 || (!r.responseStatus && !r.transferSize && !r.decodedBodySize)).map((r) => r.name.replace(location.origin, '') + ' ' + (r.responseStatus || 0));
  return { href: location.href.replace(location.origin, ''), ready: document.readyState, w: innerWidth, mock: !!window.KASKAD_MOCK, panel: !!window.KaskadPanel,
    page: p ? p.getAttribute('data-page') : null, kids: p ? p.children.length : -1, skel: document.querySelectorAll('.gx-skel').length,
    progress: !!document.querySelector('#progress:not([hidden])'), dlg: [...document.querySelectorAll('[role=dialog], .kk-tour')].map((d) => d.className).slice(0, 3),
    text: p ? p.innerText.replace(/\s+/g, ' ').slice(0, 300) : document.body.innerText.replace(/\s+/g, ' ').slice(0, 300), bad: bad.slice(0, 8) }; })()`;
async function page(hash, ready, q = "") {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru${q}#${hash}` });
  try { await until(ready, 20000); }
  catch (e) { throw new Error(e.message + "\n  страница: " + JSON.stringify(await js(PAGE_DIAG).catch((x) => String(x)))); }
  await sleep(2500);                                   // первый живой кадр мока и догрузка разделов
  // живые данные, как на стенде, меняются каждый кадр: рукопожатие ног выходов и предупреждения протоколов
  await js(`(() => { const d = window.KASKAD_MOCK.db(); let k = 0; window.__inj = setInterval(() => { k++;      // не в такт кадрам мока (2 с)
    d.exits.forEach((e) => { e._hs = 10 + 60 * (k % 5); });
    Object.keys(d.inbounds).forEach((n) => { const r = d.inbounds[n]; if (r.installed && r.enabled) r.warnings = k % 3 ? [] : ["Пример предупреждения " + k]; });
    d.devices.forEach((x) => { if (x._live) x._live.online = k % 3 !== 0; }); }, 1000); return true; })()`);
  // UI-3 ч.2, Т3: строки «Протоколов» свёрнуты — действия (кнопки) в раскрытой; раскрыть все (панель помнит раскрытые по
  // протоколу — переживает перерисовку)
  if (hash.split("?")[0] === "inbounds") await js(`(() => { document.querySelectorAll('.ux-pc__tog[aria-expanded="false"]').forEach((b) => b.click()); return true; })()`);
  // UI-A2: «Все выходы» — таблица gx-xt; действия выхода (корзина, «Выключить») — в раскрытой строке: раскрыть все (панель
  // помнит раскрытые выходы — переживает перерисовку)
  if (hash.split("?")[0] === "exits") await js(`(() => { document.querySelectorAll('.gx-xt--exa .gx-xt__x[aria-expanded="false"]').forEach((b) => b.click()); return true; })()`);
}
const res = [];
// open — открыть действие и пометить его узел (data-hold-probe); alive — то же открытое действие на месте; close — отменить
async function place(where, list, open, alive, close) {
  await until(WATCH(list));
  await sleep(4000);
  const before = await js(RB);
  if (!(await js(open))) throw new Error(where + ": не открылось");
  await sleep(150);
  const opened = await js(alive);
  await js(ZERO);
  await sleep(5000);
  const kept = await js(alive);
  await js(close);
  await sleep(200);
  const closed = !(await js(alive));
  await js(ZERO);
  await sleep(4000);
  const after = await js(RB);
  res.push({ where, before, opened, kept, closed, after });
  console.error(JSON.stringify(res[res.length - 1]));
}

// Общие помощники обеих частей (PART, выше)
const EXLIST = `.gx-tc__btns`;
const exList = `(() => { const b = document.querySelector('${EXLIST}'); let n = b; while (n && !(n.parentNode && n.parentNode.matches && n.parentNode.matches('section[data-page]'))) n = n.parentNode; return n; })()`;
const IBLIST = `(() => { const c = document.querySelector('.ux-pc'); let n = c; while (n && !(n.parentNode && n.parentNode.matches && n.parentNode.matches('section[data-page]'))) n = n.parentNode; return n; })()`;
// Ввод и мышь — как у человека (CDP Input): input-события, Enter в поле, нажатие и отпускание кнопки мыши
const typeText = (text) => cdp("Input.insertText", { text });
async function enter() {
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
}
// Точка нажатия — середина узла. R-3377-hold: тост живого сообщения мока («Выход «Токио» снова работает (пример
// сообщения alert)» — случайно, 3 % тактов мока, ~5 с внизу справа, components.css .gx-toast) ложился на корзину
// последней строки «Все выходы» (UI-A2: таблица, корзина у правого края внизу окна) — нажатие или касание получал тост,
// а не корзина, и щелчок терялся не в панели. Человек тост видит и жмёт, когда тот ушёл. Ждать его ухода (3–20 с) здесь
// нельзя: живые кадры за это время пересобирают помеченные ряды (data-probe-*), и жест сдвигается относительно кадров.
// Поэтому тост, который сверху в точке нажатия (elementFromPoint), уходит сразу — как по истечении срока: узел убираем из
// #toastHost без щелчка (щелчок мимо закрыл бы открытое подтверждение). Сверху в точке другое (слой, подложка самой
// панели) — нажатие как раньше: такую помеху проверка места ловит сама. cleared — тост убран (журнал в stderr).
async function pointOf(expr) {
  const p = await js(`(() => { const n = ${expr}; if (!n) return null; n.scrollIntoView({ block: "center" }); const r = n.getBoundingClientRect();
    const x = r.left + r.width / 2, y = r.top + r.height / 2, h = document.elementFromPoint(x, y);
    const toast = h && !n.contains(h) ? h.closest('#toastHost .gx-toast') : null;
    if (toast) toast.remove();
    return { x, y, cleared: !!toast }; })()`);
  if (!p) throw new Error("нет узла: " + expr);
  if (p.cleared) console.error("k3_hold_check: тост лежал на цели нажатия — убран: " + expr);
  return p;
}
async function mouseOn(expr) {
  const p = await pointOf(expr);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, button: "none" });
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "left", clickCount: 1 });
  await sleep(120);                                    // человек держит кнопку — таймер удержания (400 мс) тем временем идёт
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "left", clickCount: 1 });
  return p;
}
const hasText = (sel, text) => `[...document.querySelectorAll(${JSON.stringify(sel)})].some((b) => b.textContent.trim() === ${JSON.stringify(text)})`;
const TRASH = `.kk-exit .gx-trash`;

if (PART !== "2") {                                   // ══ часть 1: 1440 и 720, мышь и клавиатура ══
// ── «Выходы»: корзина плитки → подтверждение на месте (U.askDelete) ──
await page("exits", `!!document.querySelector('.gx-tc__btns .gx-trash')`);
await js(`(() => { const n = ${exList}; n.setAttribute('data-hold-list', '1'); return true; })()`);
await place("exits-confirm", "[data-hold-list]",
  `(() => { const t = document.querySelector('.gx-tc__btns .gx-trash'); t.click(); const r = document.querySelector('.is-confirm'); if (!r) return false; r.setAttribute('data-hold-probe', '1'); return true; })()`,
  `!!document.querySelector('.is-confirm[data-hold-probe]') && document.querySelector('.is-confirm[data-hold-probe]').isConnected`,
  `(() => { const r = document.querySelector('.is-confirm'); if (r) r.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);

// ── «Люди»: «⋯» карточки человека (U.menu) — список людей перерисовывается по «в сети» устройств ──
await page("people", `!!document.querySelector('.kk-ppl .gx-menu-btn')`);
await js(`(() => { let n = document.querySelector('.kk-ppl'); while (n && !(n.parentNode && n.parentNode.matches && n.parentNode.matches('section[data-page]'))) n = n.parentNode; n.setAttribute('data-hold-list', '1'); return true; })()`);
await place("people-menu", "[data-hold-list]",
  `(() => { const b = document.querySelector('[data-hold-list] .gx-menu-btn'); if (!b) return false; b.setAttribute('data-hold-probe', '1'); b.click(); return !!document.querySelector('#layers .gx-menu'); })()`,
  `!!document.querySelector('#layers .gx-menu') && !!document.querySelector('.gx-menu-btn[data-hold-probe][aria-expanded="true"]')`,
  `(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);

// ── «Протоколы»: строка ProtocolCard (ux.js, Б r3) «Удалить протокол» → подтверждение на месте ──
await page("inbounds", `!!document.querySelector('.ux-pc .ux-pc__del')`);
await js(`(() => { const n = ${IBLIST}; n.setAttribute('data-hold-list', '1'); return true; })()`);
await place("inbounds-confirm", "[data-hold-list]",
  `(() => { const c = [...document.querySelectorAll('.ux-pc')].find((x) => x.querySelector('.ux-pc__del')); if (!c) return false; c.querySelector('.ux-pc__del').click();
     const q = c.querySelector('.ux-confirm'); if (!q) return false; q.setAttribute('data-hold-probe', '1'); return true; })()`,
  `!!document.querySelector('.ux-confirm[data-hold-probe]')`,
  `(() => { const q = document.querySelector('.ux-confirm'); if (q) q.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);

// ── «Протоколы»: вопрос «Перезапустить?» в строке (VLESS упал, служба общая; Т3 — вместо открытого «⋯»: его нет) ──
await page("inbounds", `!!document.querySelector('.ux-pc')`, "&uxfail=vless:other");
await js(`(() => { const n = ${IBLIST}; n.setAttribute('data-hold-list', '1'); return true; })()`);
await place("inbounds-ask", "[data-hold-list]",
  `(() => { for (const c of document.querySelectorAll('.ux-pc')) { const b = [...c.querySelectorAll('button')].find((x) => x.textContent.trim() === 'Перезапустить'); if (!b) continue;
     b.click(); const q = c.querySelector('.ux-confirm'); if (!q) continue; q.setAttribute('data-hold-probe', '1'); return true; } return false; })()`,
  `!!document.querySelector('.ux-confirm[data-hold-probe]') && document.querySelector('.ux-confirm[data-hold-probe]').isConnected`,
  `(() => { const q = document.querySelector('.ux-confirm'); if (q) q.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);

// ── UI-1 З-3, UI-3 ч.2, Т3: раскрытая строка VLESS (список «Протоколов») переживает перерисовку списка. Перерисовку она
// не держит: строку собирают заново (rebuilt — прежнего узла нет), а раскрыта ли — помнит панель по протоколу (ibOpen) ──
{
  const CARD = `document.querySelector('[data-hold-row="ib:vless"]')`;
  const OPEN = `!!(${CARD} && ${CARD}.classList.contains('is-open') && ${CARD}.querySelector('.ux-pc__tog').getAttribute('aria-expanded') === 'true')`;
  await until(`!!(${CARD} && ${CARD}.querySelector('.ux-pc__tog'))`);
  // все раскрыты (page): свернуть все — проверка начинается со свёрнутой VLESS
  await js(`(() => { document.querySelectorAll('.ux-pc__tog[aria-expanded="true"]').forEach((b) => b.click()); return true; })()`);
  await js(`(() => { ${CARD}.querySelector('.ux-pc__tog').click(); ${CARD}.setAttribute('data-more-probe', '1'); return true; })()`);
  await sleep(150);
  const opened = await js(OPEN);
  let rebuilt = false;
  for (let i = 0; i < 40 && !rebuilt; i++) { await sleep(250); rebuilt = await js(`!document.querySelector('[data-more-probe]')`); }
  await sleep(300);
  const kept = await js(OPEN);
  const others = await js(`document.querySelectorAll('.ux-pc--row.is-open').length`);
  await js(`(() => { ${CARD}.querySelector('.ux-pc__tog').click(); ${CARD}.setAttribute('data-more-probe', '2'); return true; })()`);
  let again = false;
  for (let i = 0; i < 40 && !again; i++) { await sleep(250); again = await js(`!document.querySelector('[data-more-probe]')`); }
  const closed = await js(`!(${OPEN})`);
  res.push({ where: "inbounds-more", opened, rebuilt, kept, others, again, closed });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── «Выходы»: подтверждение пережило перерисовку, «Удалить» удаляет, плитка уходит (удержание отпущено ответом) ──
await page("exits", `!!document.querySelector('.gx-tc__btns .gx-trash')`);
{
  const n0 = await js(`document.querySelectorAll('.gx-tc__btns .gx-trash').length`);
  await js(`(() => { [...document.querySelectorAll('.gx-tc__btns .gx-trash')].pop().click(); return true; })()`);
  await sleep(4500);                                   // две живые перерисовки
  const kept = await js(`!!document.querySelector('.is-confirm')`);
  await js(`(() => { const y = document.querySelector('.is-confirm .ga-btn--danger, .is-confirm .ga-btn--danger-soft'); if (y) y.click(); return !!y; })()`);
  let gone = false;
  try { await until(`!document.querySelector('.is-confirm') && document.querySelectorAll('.gx-tc__btns .gx-trash').length === ${n0} - 1`, 15000); gone = true; } catch (e) {}
  res.push({ where: "exits-delete", kept, gone, before: n0 });
  console.error(JSON.stringify(res[res.length - 1]));
}


// ── REV-335-5 №1: «Домен и сайт» — Enter в поле домена, карточка собирается без щелчка мимо ──
await page("settings?tab=site", `!!document.querySelector('input[aria-label="Имя домена"]')`);   // UI-5: часть «Домен и сайт»
{
  const DOM = `document.querySelector('input[aria-label="Имя домена"]')`;
  const focused = await js(`(() => { const i = ${DOM}; i.setAttribute('data-hold-probe', '1'); i.scrollIntoView({ block: 'center' }); i.focus(); i.select(); return document.activeElement === i; })()`);
  const before = await js(hasText("button", "Убрать домен"));
  await typeText("vpn.example.com");
  await enter();
  let updated = false;
  try { await until(hasText("button", "Убрать домен"), 8000); updated = true; } catch (e) {}
  const rebuilt = await js(`!document.querySelector('input[data-hold-probe]')`);
  const focusNotBody = await js(`!!document.activeElement && document.activeElement !== document.body`);   // Н4
  res.push({ where: "site-enter", focused, before, updated, rebuilt, focusNotBody });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-335-5 №1: «Адрес для клиентов» — ввод, который не ушёл, на месте; Enter — карточка по сохранённому ──
await page("settings?tab=gateway", `!!document.querySelector('#stGw input')`);                    // UI-5: часть «Шлюз»
{
  const GW = `document.querySelector('#stGw input')`;
  const focused = await js(`(() => { const i = ${GW}; i.setAttribute('data-hold-probe', '1'); i.scrollIntoView({ block: 'center' }); i.focus(); i.select(); return document.activeElement === i; })()`);
  await typeText("203.0.113.77");
  // данные шлюза меняются не у этого человека (SSH-меню, другая вкладка): панель перечитывает «gw»
  const changed = await js(`window.KASKAD_MOCK.fetch('/api/gw/host', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ host: '198.51.100.9' }) }).then(() => window.KASKAD_MOCK.db().gw.host === '198.51.100.9')`);
  await sleep(3000);
  const kept = await js(`(() => { const i = document.querySelector('input[data-hold-probe]'); return !!i && i.isConnected && i.value === '203.0.113.77' && document.activeElement === i; })()`);
  await enter();
  let sent = false;
  try { await until(`!document.querySelector('input[data-hold-probe]') && !!${GW} && ${GW}.value === '203.0.113.77'`, 8000); sent = true; } catch (e) {}
  const focusKept = await js(`document.activeElement === ${GW}`);       // REV-335-6 Н4: после Enter — в новом поле, не в body
  // REV-336 З-3 (sentMove): новое поле после Enter помечено отправленным — следующие данные шлюза в нём видны, хотя фокус там
  await js(`window.KASKAD_MOCK.fetch('/api/gw/host', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ host: '198.51.100.10' }) }).then(() => window.KaskadPanel.loadAll(['gw'])).then(() => true)`);
  let nextShown = false;
  try { await until(`!!${GW} && ${GW}.value === '198.51.100.10'`, 8000); nextShown = true; } catch (e) {}
  res.push({ where: "gw-typed", focused, changed, kept, sent, focusKept, nextShown });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-335-5 №2: два подтверждения на «Выходах» — держится только своя плитка ──
await page("exits", `document.querySelectorAll('${TRASH}').length >= 3`);
{
  const n0 = await js(`document.querySelectorAll('${TRASH}').length`);
  await js(`(() => { const t = [...document.querySelectorAll('.kk-exit')]; t[0].setAttribute('data-probe-a', '1'); t[1].setAttribute('data-probe-o', '1'); return true; })()`);
  const firstOpen = await js(`(() => { document.querySelector('[data-probe-a] .gx-trash').click(); return !!document.querySelector('[data-probe-a] .is-confirm'); })()`);
  await mouseOn(`[...document.querySelectorAll('${TRASH}')].pop()`);
  await sleep(150);
  const firstClosed = await js(`!document.querySelector('[data-probe-a] .is-confirm') && document.querySelectorAll('.is-confirm').length === 1`);
  const secondOpen = await js(`(() => { const r = [...document.querySelectorAll('.kk-exit')].pop().querySelector('.is-confirm'); if (!r) return false; r.setAttribute('data-hold-probe', '1'); return true; })()`);
  await sleep(5000);                                   // две живые перерисовки
  const othersLive = await js(`!document.querySelector('[data-probe-o]')`);
  const secondKept = await js(`!!document.querySelector('.is-confirm[data-hold-probe]')`);
  const focusKept = await js(`(() => { const a = document.activeElement; return !!a && !!a.closest('.is-confirm[data-hold-probe]') && a.textContent.trim() === 'Отмена'; })()`);
  await js(`(() => { const y = document.querySelector('.is-confirm[data-hold-probe] .ga-btn--danger, .is-confirm[data-hold-probe] .ga-btn--danger-soft'); if (y) y.click(); return !!y; })()`);
  let gone = false;
  try { await until(`!document.querySelector('.is-confirm') && document.querySelectorAll('${TRASH}').length === ${n0} - 1`, 15000); gone = true; } catch (e) {}
  res.push({ where: "exits-two", n0, firstOpen, firstClosed, secondOpen, othersLive, secondKept, focusKept, gone });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── R-3377-hold: тост над корзиной уходит до нажатия, щелчок доходит до корзины (pointOf). Тост ставим прямо на корзину
//    последней строки (covered), что бы ни было с раскладкой; cleared — помощник его убрал, opened — подтверждение её ──
{
  const covered = await js(`(() => { window.KaskadPanel.toast('Проверка: тост над корзиной', '', 'info'); const n = document.querySelector('#toastHost .gx-toast');
    const t = [...document.querySelectorAll('${TRASH}')].pop(); if (!n || !t) return false; t.scrollIntoView({ block: 'center' }); const r = t.getBoundingClientRect();
    Object.assign(n.style, { left: Math.max(0, r.left - 40) + 'px', top: Math.max(0, r.top - 20) + 'px', right: 'auto', bottom: 'auto', animation: 'none' });
    const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return !!h && n.contains(h); })()`);
  const p = await mouseOn(`[...document.querySelectorAll('${TRASH}')].pop()`);
  await sleep(150);
  const opened = await js(`!![...document.querySelectorAll('.kk-exit')].pop().querySelector('.is-confirm')`);
  res.push({ where: "toast-over", covered, cleared: p.cleared, opened });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-335-5 №2: щелчок мимо и фокус вне ряда закрывают подтверждение ──
await page("exits", `!!document.querySelector('${TRASH}')`);
{
  const open = `(() => { document.querySelector('${TRASH}').click(); return !!document.querySelector('.is-confirm'); })()`;
  const opened1 = await js(open);
  await mouseOn(`document.querySelector('section[data-page] h1, section[data-page] .gx-h2, section[data-page] .kk-psec, section[data-page] .gx-xt__h')`);
  await sleep(150);
  const clickClosed = !(await js(`!!document.querySelector('.is-confirm')`));
  await sleep(600);
  const opened2 = await js(open);
  await js(`(() => { const b = [...document.querySelectorAll('section[data-page] button')].find((x) => !x.closest('.kk-exit')); b.focus(); return true; })()`);
  await sleep(150);
  const focusClosed = !(await js(`!!document.querySelector('.is-confirm')`));
  res.push({ where: "exits-away", opened1, clickClosed, opened2, focusClosed });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-335-5 №2: подтверждение без действия закрывается само через 30 с ──
await page("exits", `!!document.querySelector('${TRASH}')`);
{
  const opened = await js(`(() => { document.querySelector('${TRASH}').click(); return !!document.querySelector('.is-confirm'); })()`);
  await sleep(5000);
  const kept = await js(`!!document.querySelector('.is-confirm')`);
  await sleep(26500);
  const closed = !(await js(`!!document.querySelector('.is-confirm')`));
  const focusBack = await js(`!!document.activeElement && document.activeElement.matches('${TRASH}')`);
  res.push({ where: "exits-idle", opened, kept, closed, focusBack });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── Стенд П8: подтверждение со строкой предупреждения сверху, мышью — корзина плитки ниже ──
// плитки в одну колонку: в сетке на 1440 ряд по высоте держат соседки, и строка предупреждения ничего не сдвигает
await cdp("Emulation.setDeviceMetricsOverride", { width: 720, height: 1000, deviceScaleFactor: 1, mobile: false });   // 3.3.6: уже 700 — телефон, плитки только в детали выхода
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  const warned = await js(`(() => { const t = [...document.querySelectorAll('.kk-exit')];
    for (let i = 0; i < t.length - 1; i++) {
      t[i].querySelector('.gx-trash').click();
      // ниже — плитка, которую рост верхней сдвигает (следующий ряд сетки), а не соседка в том же ряду
      const a = t[i].getBoundingClientRect();
      const low = t[i].querySelector('.gx-confirm__warn') && t.find((x) => { const r = x.getBoundingClientRect(); return r.top >= a.bottom - 1 && Math.abs(r.left - a.left) < 2; });
      if (low) { t[i].setAttribute('data-probe-w', '1'); low.setAttribute('data-probe-b', '1'); return true; }
      const r = t[i].querySelector('.is-confirm'); if (r) r.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    }
    return false; })()`);
  const lay = await js(`(() => { window.__ev = []; ['pointerdown', 'focusin', 'mouseup', 'click'].forEach((k) => document.addEventListener(k, (e) => window.__ev.push(k + ':' + (e.target.closest && e.target.closest('[data-probe-b]') ? 'B' : 'x') + Math.round(document.querySelector('[data-probe-b]').getBoundingClientRect().top)), true)); return [...document.querySelectorAll('.kk-exit')].map((x) => { const r = x.getBoundingClientRect(); return Math.round(r.left) + ',' + Math.round(r.top) + (x.hasAttribute('data-probe-w') ? 'W' : '') + (x.hasAttribute('data-probe-b') ? 'B' : ''); }).join(' '); })()`);
  await mouseOn(`document.querySelector('[data-probe-b] .gx-trash')`);
  await sleep(150);
  const firstClosed = await js(`!document.querySelector('[data-probe-w] .is-confirm')`);
  const secondOpen = await js(`!!document.querySelector('[data-probe-b] .is-confirm')`);
  const ev = await js(`window.__ev.join(' | ')`);
  res.push({ where: "exits-warn-below", warned, firstClosed, secondOpen, ev, lay });
  console.error(JSON.stringify(res[res.length - 1]));
  await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
}

// ── REV-335-6 Н1: действие в одной карточке не отпускает несохранённое поле другой ──
// UI-5 (шаблон T13): домен и шлюз — в разных частях «Настроек», на одной странице их больше нет. Домен сохраняется в своей
// части (saved), затем в части «Шлюз» набранный адрес переживает сохранение домена (данные шлюза и сайта перечитаны)
// и смену данных шлюза снаружи (changed)
await page("settings?tab=site", `!!document.querySelector('input[aria-label="Имя домена"]')`);
{
  const DOM = `document.querySelector('input[aria-label="Имя домена"]')`, GW = `document.querySelector('#stGw input')`;
  await js(`(() => { const i = ${DOM}; i.scrollIntoView({ block: 'center' }); i.focus(); i.select(); return true; })()`);
  await typeText("vpn.example.com");
  const saved = await js(`(() => { let n = ${DOM}, b = null; while (n && !b) { n = n.parentNode; b = n && [...n.querySelectorAll('button')].find((x) => x.textContent.trim() === 'Сохранить'); }
    if (!b) return false; b.focus(); b.click(); return true; })()`);
  await sleep(300);
  await js(`(() => { location.hash = '#settings?tab=gateway'; return true; })()`);
  await until(`!!document.querySelector('#stGw input')`);
  await js(`(() => { const i = ${GW}; i.setAttribute('data-hold-probe', '1'); i.focus(); i.select(); return true; })()`);
  await typeText("203.0.113.77");
  const typed = await js(`${GW}.value === '203.0.113.77'`);
  await sleep(2500);
  const changed = await js(`window.KASKAD_MOCK.fetch('/api/gw/host', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ host: '198.51.100.9' }) }).then(() => window.KASKAD_MOCK.db().gw.host === '198.51.100.9')`);
  await sleep(3000);
  const kept = await js(`(() => { const i = document.querySelector('input[data-hold-probe]'); return !!i && i.isConnected && i.value === '203.0.113.77'; })()`);
  res.push({ where: "gw-other-card", typed, saved, changed, kept });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-335-6 Н3, Н2: подтверждения ProtocolCard (ux.js) — второе закрывает первое, щелчок мимо, 30 с ──
await page("inbounds", `document.querySelectorAll('.ux-pc .ux-pc__del').length >= 2`);
{
  const CARDS = `[...document.querySelectorAll('.ux-pc')].filter((x) => x.querySelector('.ux-pc__del'))`;
  const ask = (i) => `(() => { const c = ${CARDS}[${i}]; c.querySelector('.ux-pc__del').click(); return !!c.querySelector('.ux-confirm'); })()`;
  await js(`(() => { const c = ${CARDS}; c[0].setAttribute('data-probe-a', '1'); c[1].setAttribute('data-probe-b', '1'); return true; })()`);
  const firstOpen = await js(ask(0));
  // мышью — «Удалить протокол» второй строки: первое подтверждение закрывается, второе открывается с того же щелчка
  await mouseOn(`document.querySelector('[data-probe-b] .ux-pc__del')`);
  await sleep(150);
  const secondOpen = await js(`!!document.querySelector('[data-probe-b] .ux-confirm')`);
  const one = await js(`document.querySelectorAll('.ux-confirm').length === 1 && !document.querySelector('[data-probe-a] .ux-confirm')`);
  await mouseOn(`document.querySelector('section[data-page] h1, section[data-page] .gx-h2, section[data-page] .kk-psec, section[data-page] .gx-xt__h')`);
  await sleep(150);
  const clickClosed = !(await js(`!!document.querySelector('.ux-confirm')`));
  res.push({ where: "inbounds-two", firstOpen, secondOpen, one, clickClosed });
  console.error(JSON.stringify(res[res.length - 1]));
  await sleep(600);
  const kind = await js(`${CARDS}[0].getAttribute('data-kind')`);
  const opened = await js(ask(0));
  await sleep(31500);
  const closed = !(await js(`!!document.querySelector('.ux-confirm')`));
  const focusBack = await js(`(() => { const a = document.activeElement; return !!a && a.matches('.ux-pc__del') && !!a.closest('.ux-pc[data-kind="${kind}"]'); })()`);
  res.push({ where: "inbounds-idle", opened, closed, focusBack });
  console.error(JSON.stringify(res[res.length - 1]));
}

}
if (PART !== "1") {                                   // ══ часть 2: касание, 3.3.6, телефон ══
// ════ 3.3.6 (REV-335-7, REV-335-6 Н4/Н5, BACKLOG-3.3.6 п. 8) ════
// Плитка со строкой предупреждения (W) над другой (B) — как в exits-warn-below
const WARNED = `(() => { const t = [...document.querySelectorAll('.kk-exit')];
    for (let i = 0; i < t.length - 1; i++) {
      t[i].querySelector('.gx-trash').click();
      const a = t[i].getBoundingClientRect();
      const low = t[i].querySelector('.gx-confirm__warn') && t.find((x) => { const r = x.getBoundingClientRect(); return r.top >= a.bottom - 1 && Math.abs(r.left - a.left) < 2; });
      if (low) { t[i].setAttribute('data-probe-w', '1'); low.setAttribute('data-probe-b', '1'); return true; }
      const r = t[i].querySelector('.is-confirm'); if (r) r.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    }
    return false; })()`;
async function key(k, code, vk) {
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: k, code, windowsVirtualKeyCode: vk });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: vk });
}
const W_OPEN = `!!document.querySelector('[data-probe-w] .is-confirm')`, B_OPEN = `!!document.querySelector('[data-probe-b] .is-confirm')`;

// ── REV-335-7 Т1: телефон, касание корзины плитки ниже (pointerup раньше mousedown и ухода фокуса) ──
// 3.3.6: на телефоне (< 700) плитки выходов — только в детали; касание проверяется на самой узкой ширине с плитками в столбец
await cdp("Emulation.setDeviceMetricsOverride", { width: 720, height: 844, deviceScaleFactor: 2, mobile: true });
await cdp("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  const warned = await js(WARNED);
  const p = await pointOf(`document.querySelector('[data-probe-b] .gx-trash')`);
  await js(`(() => { window.__ev = []; ['pointerdown', 'pointerup', 'mousedown', 'focusin', 'click'].forEach((k) => document.addEventListener(k, (e) => window.__ev.push(k + ':' + (e.target.closest && e.target.closest('[data-probe-b]') ? 'B' : 'x')), true)); return true; })()`);
  await cdp("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: p.x, y: p.y }] });
  await sleep(60);
  await cdp("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(1000);                                    // дольше TOUCH_DOWN_MS
  const firstClosed = !(await js(W_OPEN)), secondOpen = await js(B_OPEN);
  res.push({ where: "exits-touch-below", warned, firstClosed, secondOpen, ev: await js(`window.__ev.join(' ')`) });
  console.error(JSON.stringify(res[res.length - 1]));
}
// ── REV-336 З-3 (pressTouch): касание держит плитку до click или TOUCH_DOWN_MS (700 мс) — кадр между pointerup и
//    запоздавшим click не уносит плитку из-под пальца. Click перехвачен до панели (как будто ещё не пришёл); кадр мока —
//    сразу (collector(true) — внеочередной такт потока) ──
const FRAME = `(() => { const d = window.KASKAD_MOCK.db(); d.exits.forEach((e) => { e._hs = 7 + Math.floor(Math.random() * 500); }); window.KASKAD_MOCK.collector(true); return true; })()`;
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  await js(`(() => { const t = [...document.querySelectorAll('.kk-exit')].pop(); t.setAttribute('data-probe-t', '1'); window.__clicks = 0;
    window.addEventListener('click', (e) => { window.__clicks++; e.stopImmediatePropagation(); e.preventDefault(); }, { capture: true, once: true }); return true; })()`);
  const p = await pointOf(`document.querySelector('[data-probe-t] .gx-trash')`);
  await cdp("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: p.x, y: p.y }] });
  await sleep(60);
  await cdp("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(30);
  await js(FRAME);
  await sleep(450);                                     // меньше TOUCH_DOWN_MS: плитка ещё ждёт click
  const kept = await js(`!!document.querySelector('[data-probe-t]')`);
  await sleep(800);
  await js(FRAME);
  let rebuilt = false;
  try { await until(`!document.querySelector('[data-probe-t]')`, 3000); rebuilt = true; } catch (e) {}
  res.push({ where: "exits-touch-hold", clicks: await js(`window.__clicks`), kept, rebuilt });
  console.error(JSON.stringify(res[res.length - 1]));
}
await cdp("Emulation.setTouchEmulationEnabled", { enabled: false });

// ── REV-335-7 П2: долгое нажатие на корзину плитки ниже, дольше живой перерисовки ──
await cdp("Emulation.setDeviceMetricsOverride", { width: 720, height: 1000, deviceScaleFactor: 1, mobile: false });   // 3.3.6: уже 700 — телефон, плитки только в детали выхода
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  await js(`(() => { const n = ${exList}; n.setAttribute('data-hold-list', '1'); return true; })()`);
  await until(WATCH("[data-hold-list]"));
  await sleep(3000);
  const live = await js(RB);                            // список живёт: без удержания перерисовка шла бы и под нажатием
  const warned = await js(WARNED);
  const p = await pointOf(`document.querySelector('[data-probe-b] .gx-trash')`);
  const top0 = await js(`document.querySelector('#main').scrollTop`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, button: "none" });
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "left", clickCount: 1 });
  await sleep(2600);
  const during = await js(`({ w: ${W_OPEN}, b: !!document.querySelector('[data-probe-b] .gx-trash'), top: document.querySelector('#main').scrollTop })`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "left", clickCount: 1 });
  await sleep(300);
  const firstClosed = !(await js(W_OPEN)), secondOpen = await js(B_OPEN);
  await js(ZERO);
  await sleep(3000);
  const after = await js(RB);
  res.push({ where: "exits-long-press", warned, live, kept: during.w && during.b, still: during.top === top0, top: [top0, during.top], firstClosed, secondOpen, after });
  console.error(JSON.stringify(res[res.length - 1]));
}
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

// ── REV-335-7 П3: правый щелчок по чужой корзине — подтверждение закрыто сразу, фокус не остаётся вне него ──
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  const opened = await js(`(() => { const t = document.querySelector('.kk-exit'); t.setAttribute('data-probe-a', '1'); t.querySelector('.gx-trash').click(); return !!t.querySelector('.is-confirm'); })()`);
  const p = await pointOf(`[...document.querySelectorAll('${TRASH}')].pop()`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, button: "none" });
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "right", clickCount: 1 });
  await sleep(150);
  const closedOnPress = !(await js(`!!document.querySelector('.is-confirm')`));
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "right", clickCount: 1 });
  await sleep(150);
  res.push({ where: "exits-right", opened, closedOnPress, none: await js(`!document.querySelector('.is-confirm')`) });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-336 З-3 (pressRight): правая кнопка — не «нажатие»: пока она зажата на корзине, плитка собирается по новым данным ──
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  await js(`(() => { [...document.querySelectorAll('.kk-exit')].pop().setAttribute('data-probe-r', '1'); return true; })()`);
  const p = await pointOf(`document.querySelector('[data-probe-r] .gx-trash')`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, button: "none" });
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "right", clickCount: 1 });
  await sleep(50);
  await js(FRAME);
  let live = false;
  try { await until(`!document.querySelector('[data-probe-r]')`, 2000); live = true; } catch (e) {}
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "right", clickCount: 1 });
  res.push({ where: "exits-right-live", live });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-335-6 Н5 / П6: Esc — фокус на корзину; выход удалили в другом месте — фокус на заголовок раздела ──
await page("exits", `document.querySelectorAll('${TRASH}').length >= 2`);
{
  const k = await js(`(() => { const t = document.querySelector('.kk-exit'); t.querySelector('.gx-trash').click(); return t.getAttribute('data-hold-row'); })()`);
  await sleep(2500);
  await key("Escape", "Escape", 27);
  await sleep(1500);                                   // ряд вернулся, блок собран по свежим данным
  const escBack = await js(`(() => { const a = document.activeElement; return !!a && a.matches('${TRASH}') && a.closest('.kk-exit').getAttribute('data-hold-row') === ${JSON.stringify(k)}; })()`);
  const id = await js(`(() => { const t = [...document.querySelectorAll('.kk-exit')].pop(); t.querySelector('.gx-trash').click(); return t.getAttribute('data-hold-row'); })()`);
  // этап 1 (SE §7.9): удаление выхода — с версией политики rev из GET /api/gw/exits, без неё мок (как сервер) отвечает 400
  const del = await js(`window.KASKAD_MOCK.fetch('/api/gw/exits', { headers: { 'X-Kaskad-Admin': '1' } }).then((r) => r.json()).then((j) => window.KASKAD_MOCK.fetch('/api/gw/exits/' + encodeURIComponent(${JSON.stringify(id)}.slice(5)) + (j && j.rev !== undefined ? '?rev=' + j.rev : ''), { method: 'DELETE', headers: { 'X-Kaskad-Admin': '1' } })).then((r) => r.ok !== false)`);
  await js(`(window.KaskadPanel.loadAll(['exits', 'people']), true)`);
  let gone = false;
  try { await until(`!document.querySelector('[data-hold-row=${JSON.stringify(id)}]')`, 15000); gone = true; } catch (e) {}
  await sleep(300);
  const goneFocus = await js(`(() => { const a = document.activeElement; return !!a && a !== document.body && a.matches('#head h1'); })()`);
  res.push({ where: "exits-focus", escBack, del, gone, goneFocus });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── BACKLOG-3.3.6 п. 8 (REV-335-5 №5): открытое «⋯» подписки в ExitsInput держит только свой ряд ──
await page("exits?tab=keys", `document.querySelectorAll('.ux-ei__item[data-hold-row] [aria-haspopup="menu"]').length >= 2`);   // nav2 r1: подписки — в «Ключах и подписках»
{
  const opened = await js(`(() => { const r = [...document.querySelectorAll('.ux-ei__item[data-hold-row]')]; r[0].setAttribute('data-probe-m', '1'); r[1].setAttribute('data-probe-o', '1');
    const b = r[0].querySelector('[aria-haspopup="menu"]'); b.click(); return b.getAttribute('aria-expanded') === 'true'; })()`);
  // данные подписок сменились (другая вкладка, SSH-меню): панель перечитала «Выходы»
  await js(`(() => { const d = window.KASKAD_MOCK.db(); d.subs.forEach((s) => { s.error = 'Сервер подписки не ответил ' + Date.now(); s.last_try = Math.floor(Date.now() / 1000); }); return true; })()`);
  await js(`window.KaskadPanel.loadAll(['exits']).then(() => true)`);
  await sleep(1500);
  const r = await js(`({ kept: !!document.querySelector('[data-probe-m] [aria-expanded="true"]') && document.querySelector('[data-probe-m]').isConnected,
    othersLive: !document.querySelector('[data-probe-o]'), menu: !!document.querySelector('#layers .gx-menu') })`);
  res.push(Object.assign({ where: "exits-sub-menu", opened }, r));
  console.error(JSON.stringify(res[res.length - 1]));
  await js(`(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);
}

// ── REV-335-7 П4: ProtocolCard — нажатие на чужую кнопку не закрывает подтверждение, закрывает щелчок;
//    П5: «Отмена» и Esc в подтверждении перезапуска общей службы — фокус на «Перезапустить» живой карточки ──
await page("inbounds", `document.querySelectorAll('.ux-pc .ux-pc__del').length >= 2`);
{
  const CARDS = `[...document.querySelectorAll('.ux-pc')].filter((x) => x.querySelector('.ux-pc__del'))`;
  await js(`(() => { const c = ${CARDS}; c[0].setAttribute('data-probe-a', '1'); c[1].setAttribute('data-probe-b', '1');
    c[0].querySelector('.ux-pc__del').click(); return true; })()`);
  const opened = await js(`!!document.querySelector('[data-probe-a] .ux-confirm')`);
  const p = await pointOf(`document.querySelector('[data-probe-b] .ux-pc__del')`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: p.x, y: p.y, button: "none" });
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: p.x, y: p.y, button: "left", clickCount: 1 });
  await sleep(150);
  const pressKept = await js(`!!document.querySelector('[data-probe-a] .ux-confirm')`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: p.x, y: p.y, button: "left", clickCount: 1 });
  await sleep(150);
  const clickClosed = !(await js(`!!document.querySelector('[data-probe-a] .ux-confirm')`));
  res.push({ where: "inbounds-press", opened, pressKept, clickClosed });
  console.error(JSON.stringify(res[res.length - 1]));
  await js(`(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); return true; })()`);
}
// VLESS упал (uxfail=vless:other): у карточки «Перезапустить», служба общая (ядро шлюза) — сначала вопрос
await page("inbounds", `!!document.querySelector('.ux-pc')`, "&uxfail=vless:other");
{
  const RS = `[...document.querySelectorAll('.ux-pc')].map((c) => [c, [...c.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Перезапустить')]).filter((x) => x[1])`;
  // Т3: строка списка — шапка и тело (кнопки) в одном .ux-pc[data-kind]
  const kind = await js(`(() => { for (const [c, b] of ${RS}) { b.click(); if (c.querySelector('.ux-confirm')) return c.getAttribute('data-kind'); } return null; })()`);
  const OF = `'.ux-pc[data-kind="' + ${JSON.stringify(kind || "")} + '"]'`;
  const RB_OF = `[...document.querySelectorAll(${OF} + ' button')].find((b) => b.textContent.trim() === 'Перезапустить')`;
  const BACK = `(() => { const a = document.activeElement; return !!a && a.isConnected && a.textContent.trim() === 'Перезапустить' && !!a.closest(${OF}); })()`;
  await sleep(4500);                                   // перерисовки: ряд карточки держит подтверждение
  const focusNo = await js(`!!document.activeElement && document.activeElement.textContent.trim() === 'Отмена'`);
  await enter();
  await sleep(1200);                                   // удержание отпущено, карточка собрана заново
  const cancelBack = await js(BACK);
  await js(`(() => { ${RB_OF}.click(); return true; })()`);
  const reopened = await js(`!!document.querySelector('.ux-confirm')`);
  await sleep(300);
  await key("Escape", "Escape", 27);
  await sleep(1200);
  const escClosed = await js(`!document.querySelector('.ux-confirm')`);
  const escBack = await js(BACK);
  res.push({ where: "inbounds-restart", kind, focusNo, cancelBack, reopened, escClosed, escBack });
  console.error(JSON.stringify(res[res.length - 1]));
}

// ── REV-336 С-1: подсветка «вот оно» (is-flash) видна — вычисленная тень не none — в тёмной и светлой теме и у карточек
//    border/flat (там тень карточки — none, и список «…, none» отбрасывал правило целиком) ──
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
async function flashOf(look, hash, sel) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#overview` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page]')`, 20000);
  await js(`(localStorage.setItem("kaskad-appearance-v1", ${JSON.stringify(JSON.stringify(Object.assign({ v: 3 }, look)))}), true)`);
  const uid = await js(`window.KASKAD_MOCK.db().users[0].id`);
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  const on = JSON.stringify(sel + ".is-flash");
  // REV-336-mobile П-9, REV-336-awgaddr: подсветку ловим событием в самой странице, а не опросом. Скрипт ставится ДО
  // загрузки (addScriptToEvaluateOnNewDocument): MutationObserver на весь документ — в ту же задачу, где у узла появился
  // класс is-flash, переход тени доводится до конца (finish) и тень читается. Опрос раз в 50 мс и ожидание
  // getAnimations().finished под нагрузкой (параллельный Chrome) опаздывали: подсветка (2 с) сходила или узел
  // перерисовывался — «gone», «seen: false». Требование прежнее: тень видна, кольцо ≥ 2 px
  await cdp("Page.enable");                            // без него скрипт «до загрузки» Chrome не ставит
  const { identifier } = await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
    const at = (n) => { if (window.__flash || !n || !n.matches || !n.matches(${on})) return;
      n.getAnimations().forEach((a) => { try { a.finish(); } catch (e) { /* бесконечная — как есть */ } });
      const r = document.documentElement;
      window.__flash = { seen: true, theme: r.getAttribute('data-theme'), cards: r.getAttribute('data-cards'), sh: getComputedStyle(n).boxShadow }; };
    new MutationObserver((list) => { for (const m of list) {
      if (m.type === 'attributes') at(m.target);
      else for (const n of m.addedNodes) { if (n.nodeType !== 1) continue; at(n); if (!window.__flash && n.querySelector) at(n.querySelector(${on})); } } })
      .observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  })()` });
  try {
    await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#${hash.replace("{uid}", uid)}` });
    return await js(`new Promise((done) => { const end = Date.now() + 20000;
      const look = () => { if (window.__flash) done(window.__flash); else if (Date.now() < end) setTimeout(look, 50); else done({ seen: false }); };
      look(); })`);
  } finally {
    await cdp("Page.removeScriptToEvaluateOnNewDocument", { identifier });
    await cdp("Page.disable");
  }
}
{
  const r = { where: "flash", cases: [] };
  const PLACES = [["settings?focus=settings.bot&open=bot", "[data-find]"], ["people?focus={uid}", ".gx-xt__r[data-uid]"], ["settings?open=host", "#stGwAddr"]];   // круг 1 П4: адрес для клиентов — блок «Шлюза»
  for (const look of [{ theme: "dark" }, { theme: "light" }, { theme: "dark", cards: "border" }, { theme: "light", cards: "flat" }])
    for (const [hash, sel] of PLACES) r.cases.push(Object.assign({ look: JSON.stringify(look), hash, sel }, await flashOf(look, hash, sel)));
  res.push(r);
  console.error(JSON.stringify(r));
}

// ── REV-336-2 З-1 и П-4: переход к карточке из поиска, мастера и с карты — карточка на экране, когда раздел дособран по
//    данным (на 390 выше вставлялись карточки шлюза и сайта, и прокрутка доезжала до старого места). 390 и 1440×720:
//    через 3 с верх цели (у человека — его имя) в видимой части области #main; на 1440 у «Бота» — у верха (≤ 140 px) ──
{
  const r = { where: "flash-view", cases: [] };
  const PLACES = [["settings?focus=settings.bot&open=bot", '[data-find="settings.bot"]'], ["settings?open=bot", '.ux-fold[data-ux="bot"]'],
    ["settings?open=mcp", '.ux-fold[data-ux="agent"]'], ["people?focus={uid}", '[data-uid="{uid}"] .kk-pname, [data-uid="{uid}"] .kk-prow__n']];
  for (const [w, h, mob] of [[390, 844, true], [1440, 720, false]]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: mob });
    for (const [hash, sel0] of PLACES) {
      await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
      await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#overview` });
      await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page]')`, 20000);
      await js(`(localStorage.setItem("kaskad-appearance-v1", JSON.stringify({ v: 3, theme: "dark" })), true)`);
      const uid = await js(`window.KASKAD_MOCK.db().users[0].id`);
      const sel = sel0.split("{uid}").join(uid);
      await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
      await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#${hash.split("{uid}").join(uid)}` });
      await sleep(3200);
      r.cases.push(Object.assign({ w, hash }, await js(`(() => { const n = [...document.querySelectorAll(${JSON.stringify(sel)})].find((x) => x.offsetParent !== null);
        const m = document.querySelector('#main'); if (!n || !m) return { found: !!n };
        const a = n.getBoundingClientRect(), b = m.getBoundingClientRect();
        return { found: true, top: Math.round(a.top - b.top), h: Math.round(b.height), seen: a.top >= b.top - 2 && a.top <= b.bottom - 40 }; })()`)));
    }
  }
  res.push(r);
  console.error(JSON.stringify(r));
}

// ── REV-336-mobile С-1: телефон 390 — старый адрес части «Настроек» (мастер ?open=bot|mcp, «Шлюз» ?open=host, поиск
//    ?focus=settings.*) открывает часть, адрес становится ?tab=<часть> (part), «‹ Настройки» ведёт к списку частей
//    (list: адрес #settings, строки частей на экране) ──
{
  const r = { where: "settings-back", cases: [] };
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  for (const [hash, tab] of [["settings?open=bot", "bot"], ["settings?open=mcp", "bot"], ["settings?open=host", "gateway"],
    ["settings?focus=settings.bot&open=bot", "bot"], ["settings?focus=settings.mcp", "bot"]]) {
    await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
    await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#${hash}` });
    await until(`!!window.KASKAD_MOCK && !!document.querySelector('#head .gx-mnav__back')`, 20000);
    await sleep(1500);
    const part = await js(`location.hash`);
    const title = await js(`(document.querySelector('#head .kk-mhead__t') || {}).textContent || ''`);
    await js(`(document.querySelector('#head .gx-mnav__back').click(), true)`);
    await sleep(800);
    const after = await js(`({ hash: location.hash, rows: document.querySelectorAll('section[data-page="settings"] [data-hold-row^="st:"]').length,
      title: (document.querySelector('#head .kk-mhead__t') || {}).textContent || '' })`);
    r.cases.push({ hash, want: "#settings?tab=" + tab, part, title, after });
  }
  res.push(r);
  console.error(JSON.stringify(r));
}

// ── REV-336-mobile С-2: «Поделиться» на телефоне — адреса страницы и подписки приходят при открытии листа (early —
//    «Скопировать» недоступна, ready — доступна), копирование — в самом нажатии: буфер как в Safari (только синхронно в
//    обработчике щелчка). ok — скопировано, лист закрыт; deny — буфер отказал: адрес полем в листе, выделен; closed —
//    страница включена, полного адреса нет — «Подробнее» (её окно) ──
// пункт «Поделиться» открытого «⋯» (на телефоне меню — лист)
const SHARE_ITEM = `[...document.querySelectorAll('#layers button, #layers [role=menuitem]')].find((b) => /^Поделиться/.test(b.textContent.trim()))`;
{
  const r = { where: "share-copy", cases: [] };
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  for (const mode of ["ok", "deny", "closed"]) {
    await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
    await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#people` });
    await until(`!!window.KASKAD_MOCK && !!document.querySelector('[data-hold-row^="user:"]')`, 30000);
    await sleep(1500);
    // страница и подписка включены; полный адрес (в моке он есть только в открытом режиме, а тот требует входа) —
    // подставляем в ответ GET; closed — без адреса
    const uid = await js(`(async () => { const M = window.KASKAD_MOCK, f0 = M.fetch, u = M.db().users.find((x) => x.enabled);
      for (const k of ['page', 'sub']) await f0('/api/gw/users/' + u.id + '/' + k, { method: 'POST', headers: { 'content-type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ action: 'enable' }) });
      window.__ppl = false;
      M.fetch = (url, init) => f0(url, init).then((r) => {
        const get = !(init && init.method && init.method !== 'GET');
        if (${mode !== "closed"} && get && /\\/(page|sub)$/.test(String(url))) {
          const tx = r.text.bind(r);
          r.text = async () => { const o = JSON.parse(await tx()); if (o.enabled) o.url = 'https://vpn.example.com/p/' + String(url).split('/').slice(-1)[0] + '-' + u.id; return JSON.stringify(o); };
        }
        if (String(url).indexOf('/api/gw/people') >= 0) setTimeout(() => { window.__ppl = true; }, 300);
        return r;
      });
      return u.id; })()`);
    await until(`window.__ppl`, 20000);
    await sleep(1500);
    // UI-B 23.10: «Поделиться» — из «⋯» строки человека (лист), не из детали во весь экран
    await until(`!!document.querySelector('.kk-prow[data-uid="' + ${JSON.stringify(uid)} + '"] .kk-prow__more')`, 30000);
    // буфер как в Safari: только из самого нажатия (синхронно в обработчике); deny — отказ всегда
    await js(`(() => { window.__g = false; window.__copied = []; document.addEventListener('click', () => { window.__g = true; setTimeout(() => { window.__g = false; }, 0); }, true);
      navigator.clipboard.writeText = (s) => (${mode !== "deny"} && window.__g ? (window.__copied.push(s), Promise.resolve()) : Promise.reject(new DOMException('no', 'NotAllowedError'))); return true; })()`);
    await js(`(document.querySelector('.kk-prow[data-uid="' + ${JSON.stringify(uid)} + '"] .kk-prow__more').click(), true)`);
    await until(SHARE_ITEM, 15000);
    await js(`(${SHARE_ITEM}.click(), true)`);
    await until(`!!document.querySelector('.gx-bsheet .gx-mrow .ga-btn')`, 15000);
    const early = await js(`[...document.querySelectorAll('.gx-bsheet .gx-mrow .ga-btn')].map((b) => b.textContent + (b.disabled ? ':dis' : ''))`);
    await until(`[...document.querySelectorAll('.gx-bsheet .gx-mrow .ga-btn')].filter((b) => !b.disabled).length >= 2`, 15000);
    const ready = await js(`[...document.querySelectorAll('.gx-bsheet .gx-mrow .ga-btn')].map((b) => b.textContent)`);
    const subs = await js(`[...document.querySelectorAll('.gx-bsheet .gx-mrow .gx-mrow__s')].map((n) => n.textContent)`);
    await js(`(document.querySelector('.gx-bsheet .gx-mrow .ga-btn').click(), true)`);
    await sleep(600);
    r.cases.push({ mode, early, ready, subs, copied: await js(`window.__copied`), sheet: await js(`!!document.querySelector('.gx-bsheet')`), dlg: await js(`document.querySelectorAll('.gx-modal').length`),
      field: await js(`(() => { const f = document.querySelector('.gx-bsheet .gx-mrow__url'); if (!f) return null; const r = f.getBoundingClientRect(), b = f.closest('.gx-bsheet').getBoundingClientRect();
        return { v: f.value, focus: document.activeElement === f, sel: f.selectionEnd - f.selectionStart, h: Math.round(r.height), inside: r.left >= b.left && r.right <= b.right }; })()`) });
  }
  res.push(r);
  console.error(JSON.stringify(r));
}

// ── REV-336-mobile З-1, З-3: 390 — строки «Ещё» 52 px и строки листа 60 px (по макету, не 44); лист «Поделиться»
//    смахивается вниз касанием за шапку (touch-action: none, захват указателя) ──
{
  const out = { where: "sheet-swipe" };
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await cdp("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#more` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page="more"] .gx-mrow')`, 30000);
  await sleep(800);
  out.more = await js(`[...document.querySelectorAll('section[data-page="more"] .gx-mrow')].slice(0, 4).map((n) => [n.className, Math.round(n.getBoundingClientRect().height)])`);
  await js(`(location.hash = '#people', true)`);
  await until(`!!document.querySelector('.kk-prow .kk-prow__more')`, 30000);
  await js(`(document.querySelector('.kk-prow .kk-prow__more').click(), true)`);
  await until(SHARE_ITEM, 15000);
  await js(`(${SHARE_ITEM}.click(), true)`);
  await until(`!!document.querySelector('.gx-bsheet .gx-mrow .ga-btn')`, 15000);
  await sleep(400);
  out.sheetRows = await js(`[...document.querySelectorAll('.gx-bsheet .gx-mrow')].map((n) => Math.round(n.getBoundingClientRect().height))`);
  // З-2: значки «акцент» — цвет акцента, не «работает»
  out.icons = await js(`[...document.querySelectorAll('.gx-bsheet .gx-mrow__ic')].map((n) => n.className + '|' + (getComputedStyle(n).color === getComputedStyle(document.documentElement).getPropertyValue('--accent-text').trim() ? 'acc' : getComputedStyle(n).color))`);
  const g = await js(`(() => { const r = document.querySelector('.gx-bsheet__h').getBoundingClientRect(); return { x: Math.round(r.left + 60), y: Math.round(r.top + r.height / 2) }; })()`);
  await js(`(window.__ev = [], ['pointerdown', 'pointermove', 'pointerup', 'pointercancel'].forEach((k) => document.querySelector('.gx-bsheet__h').addEventListener(k, () => window.__ev.push(k))), true)`);
  await cdp("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: g.x, y: g.y }] });
  for (let i = 1; i <= 12; i++) { await cdp("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: g.x, y: g.y + i * 15 }] }); await sleep(16); }
  await cdp("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(500);
  out.swipe = await js(`({ open: !!document.querySelector('.gx-bsheet'), ev: [...new Set(window.__ev)] })`);
  await cdp("Emulation.setTouchEmulationEnabled", { enabled: false });
  res.push(out);
  console.error(JSON.stringify(out));
}

// ── REV-336-mobile З-4, З-5, З-6, З-8, П-2 (390 и 768): «Требует внимания» — что случилось и причина второй строкой,
//    поломка протокола ведёт к нему; плитка «Шлюз» — в часть ?tab=gateway; число устройств у человека — не по фильтру;
//    подпись строки для диктора — с состоянием; одна акцентная кнопка на экране человека ──
{
  const out = { where: "phone-ux" };
  const go = async (w, h, mob, hash, ready) => {
    await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: mob });
    await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
    await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#${hash}` });
    await until(`!!window.KASKAD_MOCK && !!document.querySelector(${JSON.stringify(ready)})`, 30000);
    await sleep(2500);
  };
  // круг 1 Т1 (Сводка варианта Б): замечания — строки «Сервер сейчас» (kb-iss__r: что, почему, кнопка действия); плиток нет
  await go(390, 844, true, "overview", ".kb-hero");
  out.att = await js(`[...document.querySelectorAll('section[data-page="overview"] .kb-iss__r')].map((n) => [n.querySelector('.kb-iss__b > b').textContent, (n.querySelector('.kb-iss__b > span') || {}).textContent || '', (n.querySelector('button') || {}).tagName || '', n.getAttribute('data-ovs')])`);
  const down = await js(`(() => { const r = [...document.querySelectorAll('section[data-page="overview"] .kb-iss__r')].find((n) => /не работает/.test(n.textContent) && /^(ib|p):/.test(n.getAttribute('data-ovs') || '')); const b = r && r.querySelector('button'); if (!b) return null; b.click(); return true; })()`);
  await sleep(1500);
  // Т3 (Протоколы Б r3): детали во весь экран на телефоне нет — строка протокола раскрыта на месте, focus снят с адреса
  out.downGo = [down, await js(`location.hash`), await js(`[...document.querySelectorAll('section[data-page="inbounds"] [data-kind].is-open')].map((n) => n.dataset.kind)`)];
  await go(390, 844, true, "people", "[data-hold-row^='user:']");
  out.all = await js(`[...document.querySelectorAll('[data-hold-row^="user:"]')].map((n) => (n.querySelector('.kk-prow__s, .kk-prow__sp') || {}).textContent + ' # ' + n.querySelector('.kk-prow__main').getAttribute('aria-label'))`);
  out.allIds = await js(`[...document.querySelectorAll('[data-hold-row^="user:"]')].map((n) => n.getAttribute('data-hold-row'))`);
  // nav2 r1 (UI-A1): фильтр «Людей» на телефоне — лист «Показать ▾» и адрес #people?show=…; «В сети» — адресом;
  // строки — UI-B (kk-prow)
  await go(390, 844, true, "people?show=on", "[data-hold-row^='user:']");
  out.online = await js(`[...document.querySelectorAll('[data-hold-row^="user:"]')].map((n) => (n.querySelector('.kk-prow__s, .kk-prow__sp') || {}).textContent)`);
  // строки — по id (3.3.7.10: объём за сутки в макете растёт с открытым окном сборщика — текст между двумя чтениями другой)
  out.onlineIds = await js(`[...document.querySelectorAll('[data-hold-row^="user:"]')].map((n) => n.getAttribute('data-hold-row'))`);
  const acc = `[...document.querySelectorAll('#main .ga-btn--primary, #head .ga-btn--primary')].filter((b) => b.offsetParent).map((b) => b.textContent)`;
  out.person = {};
  for (const [w, h, mob, view] of [[390, 844, true, "phone"], [768, 1024, false, "tablet"]]) {
    for (const who of ["u-1a2b3c4d", "u-9c0d1e2f"]) {
      await go(w, h, mob, "people?focus=" + who, ".gx-bsheet--act");     // UI-B: человек — листом
      out.person[view + ":" + who] = await js(acc);
    }
  }
  res.push(out);
  console.error(JSON.stringify(out));
}

// ── REV-336-mobile З-7 (390): форма «+ Ключ» во весь экран — над вкладками (полоса у их верха, вкладки видны);
//    протокол (Т3, Б r3 Т2; было З-9 — лист «⋯»): строка раскрывается на месте, действия — кнопками в ней ──
{
  const out = { where: "phone-forms" };
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#exits` });
  await sleep(4000);
  await until(`!!window.KASKAD_MOCK && !!document.querySelector("#head .ga-btn--primary")`, 30000);
  await sleep(1500);
  await js(`(document.querySelector('#head .ga-btn--primary').click(), true)`);
  await until(`!!document.querySelector('.gx-dlg-bd--sheet .gx-dlg__foot')`, 15000);
  await sleep(600);
  out.form = await js(`(() => { const f = document.querySelector('.gx-dlg-bd--sheet .gx-dlg__foot').getBoundingClientRect(), t = document.querySelector('#tabs').getBoundingClientRect();
    return { footBottom: Math.round(f.bottom), tabsTop: Math.round(t.top), tabsH: Math.round(t.height), vh: innerHeight,
      tabsOpacity: Number(getComputedStyle(document.querySelector('#tabs')).opacity), inert: !!document.querySelector('#app').inert }; })()`);
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#inbounds` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector("section[data-page='inbounds'] .ux-pc--row .ux-pc__tog")`, 30000);
  await sleep(1000);
  await js(`(() => { document.querySelectorAll("section[data-page='inbounds'] .ux-pc--row .ux-pc__tog[aria-expanded='false']").forEach((b) => b.click()); return true; })()`);
  await sleep(800);
  out.row = await js(`(() => { const sec = document.querySelector('section[data-page="inbounds"]'), rows = [...sec.querySelectorAll('.ux-pc--row')];
    const vis = (n) => !!n && n.getClientRects().length > 0, btns = rows.flatMap((g) => [...g.querySelectorAll('.ux-pc__acts button')]).filter(vis);
    return { rows: rows.length, open: rows.filter((g) => g.classList.contains('is-open') && vis(g.querySelector('.ux-pc__btns'))).length,
      sheet: !!document.querySelector('.gx-bsheet'), menus: sec.querySelectorAll('[aria-haspopup="menu"]').length, btns: btns.length,
      minH: btns.length ? Math.min(...btns.map((b) => Math.round(b.getBoundingClientRect().height))) : 0,
      wide: btns.filter((b) => b.getBoundingClientRect().right > innerWidth + 0.5).length,
      roles: [...sec.querySelectorAll('.ux-pc--row [aria-checked]')].map((n) => n.getAttribute('role')) }; })()`);
  res.push(out);
  console.error(JSON.stringify(out));
}

// ── REV-336-mobile-2 З-10: лист «⋯» человека на низком экране (телефон боком, разделённый экран) — пункты не
//    срезаны: последний виден целиком или тело листа прокручивается до него ──
{
  const out = { where: "sheet-low", cases: [] };
  for (const h of [360, 300]) {
    await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: h, deviceScaleFactor: 1, mobile: true });
    await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
    await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#people` });
    await until(`!!window.KASKAD_MOCK && !!document.querySelector('[data-hold-row^="user:"]')`, 30000);
    await sleep(1000);
    // UI-B 23.10: «⋯» — у строки человека в списке (детали во весь экран больше нет)
    await until(`!!document.querySelector('.kk-prow .kk-prow__more')`, 30000);
    await sleep(500);
    await js(`(document.querySelector('.kk-prow .kk-prow__more').click(), true)`);
    await until(`!!document.querySelector('.gx-bsheet .gx-mrow')`, 15000);
    await sleep(500);
    out.cases.push(Object.assign({ h }, await js(`(() => { const b = document.querySelector('.gx-bsheet__b'), rows = [...b.querySelectorAll('.gx-mrow')], last = rows[rows.length - 1];
      b.scrollTop = b.scrollHeight; const br = b.getBoundingClientRect(), lr = last.getBoundingClientRect();
      const lists = [...b.querySelectorAll('.gx-mlist')].map((l) => l.scrollHeight - l.clientHeight);
      return { rows: rows.length, scrolls: b.scrollHeight > b.clientHeight, lastSeen: lr.top >= br.top - 1 && lr.bottom <= br.bottom + 1, clipped: Math.max(0, ...lists) }; })()`)));
  }
  res.push(out);
  console.error(JSON.stringify(out));
}

// ── REV-336-mobile-2 П-10: строка проброса на телефоне — живые счётчики во второй строке; подпись для диктора их не
//    копирует (замёрзла бы на «—»), вторая строка связана описанием ──
{
  const out = { where: "live-label" };
  await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#forwards` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('button.gx-mrow[data-hold-row^="fw:"]')`, 30000);
  await sleep(800);
  out.rows = await js(`[...document.querySelectorAll('button.gx-mrow[data-hold-row^="fw:"]')].map((n) => { const d = n.getAttribute('aria-describedby'), s = d && document.getElementById(d);
    return { label: n.getAttribute('aria-label'), desc: !!s && n.contains(s), sub: (n.querySelector('.gx-mrow__s') || {}).textContent || '' }; })`);
  res.push(out);
  console.error(JSON.stringify(out));
}

}
console.log(JSON.stringify(res));
ws.close(); stop(); process.exit(0);
