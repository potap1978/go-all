// k3_order_check.mjs — PLAN-order Ш4–Ш5: порядок в группах в настоящем Chrome на заглушке панели (?mock=1&ord=…).
// Что проверяется (tests/test_panel_order_chrome.py читает JSON из stdout):
//   rows    — «Основная», уровень 1 «Лучший отклик» (11 / 95 / 12 / 12 мс): строки по рядам сервера — Ключ 1, 3, 4, затем
//             Ключ 2 (Р2); метки рядов, число отклика, строка под шапкой; у каждой строки ручка и ↑↓;
//   mouse   — мышью: «Ключ 2» за ручку — выше «Ключа 1»: первым, уровень «По порядку (вручную)», строка «Автовыбор
//             выключен…», «Изменений: 1»; «Сохранить» — в политике заглушки order: manual и порядок как на экране;
//   key     — клавиатура: ручка «Ключа 3», пробел, ↑, пробел — «Ключ 3» выше «Ключа 1»; Esc после ↑ — как было;
//   touch   — палец на 768: удержание ручки «Ключа 4» и ведение вверх — первым; вне ручки страница прокручивается;
//   touch390 — палец на 390 (телефон: «Группы» там не показываются, внутренняя группа — да): «Дача» на «Роутер» — шаги местами;
//   conn    — «Основная» по соединениям: ручки строк неактивны, перетаскивание ничего не меняет;
//   steps   — внутренняя группа «Дом» (rug=ok): ↑ у «Дачи» — шаги местами (Дача, Роутер), а не два ключа в одном шаге;
//   stepWas — (Н-2 стенда 3.3.7.13) «Дом» с двумя ключами в одном шаге (rug=tier): ↑ внутри шага — «было: Р1» у ключей;
//   bar390  — (М-2 стенда 3.3.7.13) телефон: в полосе правок «Что изменится» над кнопками, кнопки поровну, без пустой полосы;
//   Ш6 (вид по макету Claude Design r1): в rows — причины рядов и полоски силы (лучший / свой, %); в mouse — «было: …»;
//   zone    — мышью «Ключ 2» в уровень «Запасной» (автоуровень): линии нет, у уровня рамка, подпись «встанет по силе»;
//   flip    — после ↓ строки переезжают анимацией (FLIP); с «уменьшить движение» — без неё.
//   REV-order (29.10): outside — «Ключ 2» мышью под «+ Уровень» и выше карточки, «Ключ 4» пальцем на 768 под «+ Уровень»:
//             «сюда нельзя» (строка и подпись is-no), отпустили — порядок прежний (С-1); merge — внутренняя группа
//             «Дом», «Дача» мышью на строку «Роутер» (40 % высоты): «в один шаг с «Роутер»», шаг подсвечен (is-target),
//             строка в «Что изменится»; на EN — «one step with “Router”» (С-2); manualOut — «Запасной» «По порядку
//             (вручную)», ↓ у «Вены», «Сохранить»: без «Не участвуют» посреди очереди, без ложного «было» (С-3);
//             в key — aria-pressed у поднятой ручки (П-2).
// node tools/k3_order_check.mjs <адрес статики панели> <chrome>   (K3_SHOT=<папка> — ещё снимки экрана)
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3], SHOT = process.env.K3_SHOT || "";
const prof = mkdtempSync(join(tmpdir(), "k3ord-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_order_check: время вышло"); process.exit(3); }, 240000).unref();
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
const ERR = [];
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); }
  // несохранённый порядок — «Уйти со страницы?» при переходе к следующей сцене: уйти (иначе Page.navigate ждёт вечно)
  if (m.method === "Page.javascriptDialogOpening") ws.send(JSON.stringify({ id: ++seq, method: "Page.handleJavaScriptDialog", params: { accept: true } }));
  if (m.method === "Runtime.exceptionThrown") ERR.push(String(m.params.exceptionDetails.exception && m.params.exceptionDetails.exception.description || m.params.exceptionDetails.text).slice(0, 300)); });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error("js: " + JSON.stringify(r.exceptionDetails).slice(0, 400)); return r.result.value; };
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* грузится */ } await sleep(120); } throw new Error("не дождался: " + expr); }
await cdp("Runtime.enable");
await cdp("Page.enable");
async function size(w, h, phone) {
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: !!phone });
  await cdp("Emulation.setTouchEmulationEnabled", { enabled: !!phone, maxTouchPoints: phone ? 5 : 1 });
}
// открыть вкладку и раскрыть карточку группы gid (карточки по умолчанию свёрнуты)
async function open(qs, hash, gid) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1${qs || ""}#${hash}` });
  await until(`!!window.KASKAD_MOCK && !!window.KaskadPanel`, 20000);
  await until(`!!${CARD(gid)}`, 20000);
  await js(`(() => { const b = ${CARD(gid)}.querySelector('.kk-eg-tog[aria-expanded="false"]'); if (b) b.click(); return true; })()`);
  await until(READY(gid), 20000);
  await sleep(500);
}
async function shot(name) {
  if (!SHOT) return;
  const r = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  writeFileSync(join(SHOT, "order-" + name + ".png"), Buffer.from(r.data, "base64"));
}
const CARD = (gid) => `document.querySelector('[data-find="exits.group.${gid}"]')`;
const READY = (gid) => `!!(${CARD(gid)} && ${CARD(gid)}.querySelector('[data-ord-sec]'))`;
// уровни карточки: строки (имя, метка, число, ручка неактивна?), строка под шапкой, предупреждение ручного порядка
const TIERS = (gid) => `[...${CARD(gid)}.querySelectorAll('[data-ord-sec]')].map((s) => ({ id: s.dataset.ordSec,
  rows: [...s.querySelectorAll('[data-ord-row]')].map((r) => r.querySelector('.kk-eg-exit__n > b').textContent),
  tags: [...s.querySelectorAll('[data-ord-row] .kk-eg-rk')].map((x) => x.textContent),
  ms: [...s.querySelectorAll('[data-ord-row] .kk-eg-rtt')].map((x) => x.textContent),
  why: [...s.querySelectorAll('.ko-why .ko-why__t')].map((x) => x.textContent),
  bars: [...s.querySelectorAll('[data-ord-row] .ko-bar')].map((b) => (b.firstElementChild ? Math.round(parseFloat(b.firstElementChild.style.width)) : null)),
  dirty: s.querySelectorAll('[data-ord-row].is-dirty').length,
  grips: [...s.querySelectorAll('[data-ord-row] [data-ord-grip]')].map((g) => g.getAttribute('aria-disabled') === 'true' ? 'off' : 'on'),
  arrows: s.querySelectorAll('[data-ord-row] .kk-eg-ord > button:not([data-ord-grip])').length,
  arrowsOn: [...s.querySelectorAll('[data-ord-row] .kk-eg-ord > button:not([data-ord-grip])')].filter((x) => !x.disabled).map((x) => x.getAttribute('aria-label')),
  note: (s.querySelector('.kk-eg-rkn') || {}).textContent || '', man: (s.querySelector('.kk-eg-man') || {}).textContent || '',
  method: (s.querySelector('.kk-eg-tier__pw select') || {}).value || '' }))`;
const DIRTY = (gid) => `(() => { const b = ${CARD(gid)}.querySelector('.kk-sb'); return b && !b.hidden ? (b.querySelector('.kk-sb__n') || {}).textContent || '' : ''; })()`;
// середина элемента в окне (прокрутив к нему)
const AT = (sel) => `(() => { const n = ${sel}; n.scrollIntoView({ block: 'center' }); const r = n.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, bottom: r.bottom }; })()`;
const GRIP = (gid, name) => `[...${CARD(gid)}.querySelectorAll('[data-ord-row]')].find((r) => r.querySelector('.kk-eg-exit__n > b').textContent === '${name}').querySelector('[data-ord-grip]')`;
const ROW = (gid, name) => `[...${CARD(gid)}.querySelectorAll('[data-ord-row]')].find((r) => r.querySelector('.kk-eg-exit__n > b').textContent === '${name}')`;
async function mouseDrag(gid, from, to) {
  const g = await js(AT(GRIP(gid, from)));
  const r = await js(`(() => { const r = ${ROW(gid, to)}.getBoundingClientRect(); return { y: r.top + 4 }; })()`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: g.x, y: g.y });
  await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: g.x, y: g.y, button: "left", buttons: 1, clickCount: 1 });
  for (let i = 1; i <= 10; i++) { await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: g.x, y: g.y + (r.y - g.y) * i / 10, button: "left", buttons: 1 }); await sleep(30); }
  const line = await js(`(() => { const l = ${CARD(gid)}.querySelector('.gx-ord-line'); return l ? { shown: !l.hidden, no: l.classList.contains('is-no'), drag: !!${CARD(gid)}.querySelector('.is-ord-drag'),
    zone: l.classList.contains('is-zone'), text: l.textContent, drop: [...${CARD(gid)}.querySelectorAll('[data-ord-sec].is-drop')].map((n) => n.dataset.ordSec),
    lifted: !!${CARD(gid)}.querySelector('[data-ord-row].is-lifted') } : null; })()`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x: g.x, y: r.y, button: "left", buttons: 0, clickCount: 1 });
  await sleep(300);
  return line;
}
async function key(k, code, vk) {
  await cdp("Input.dispatchKeyEvent", { type: "rawKeyDown", key: k, code, windowsVirtualKeyCode: vk });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, code, windowsVirtualKeyCode: vk });
  await sleep(120);
}
const SPACE = [" ", "Space", 32], UP = ["ArrowUp", "ArrowUp", 38], ESC = ["Escape", "Escape", 27];
const out = {};
const step = (n) => { if (process.env.K3_DEBUG) console.error("шаг:", n); };

step('rows + mouse');
// rows + mouse
await size(1440, 900);
await open("&ord=1", "exits?tab=groups", "main");
out.rows = (await js(TIERS("main")))[0];
await shot("rows-1440");
out.mouse = { line: await mouseDrag("main", "Ключ 2", "Ключ 1") };
out.mouse.tier = (await js(TIERS("main")))[0];
out.mouse.dirty = await js(DIRTY("main"));
await shot("manual-1440");
const save = await js(`(() => { const b = [...${CARD("main")}.querySelectorAll('.kk-sb button')].find((x) => x.textContent.trim() === 'Сохранить' && !x.disabled); if (b) b.click(); return !!b; })()`);
const POL = `window.KASKAD_MOCK.db().policy.groups.find((g) => g.id === 'main').tiers[0]`;
await until(`${POL}.order === 'manual'`, 10000).catch(() => null);
out.mouse.saved = await js(`(() => { const t = ${POL}; return { clicked: ${save}, order: t.order || null, method: t.method, exits: t.exits }; })()`);

step('zone');
// zone — в автоуровень место не важно: линии нет, рамка у уровня, «встанет по силе»; после — строка в уровне 2, без ручного
await open("&ord=1", "exits?tab=groups", "main");
out.zone = { line: await mouseDrag("main", "Ключ 2", "Вена") };
out.zone.tiers = (await js(TIERS("main"))).map((x) => ({ rows: x.rows, method: x.method }));
out.zone.dropLeft = await js(`${CARD("main")}.querySelectorAll('.is-drop').length`);

step('flip');
// flip — ↓ у «Ключа 1»: строки переезжают анимацией; «уменьшить движение» — сразу на месте
const ANIM = `document.getAnimations().filter((a) => a.effect && a.effect.target && a.effect.target.closest && a.effect.target.closest('[data-ord-root]')).length`;
const DOWN1 = `${ROW("main", "Ключ 1")}.querySelector('.kk-eg-ord > button:not([data-ord-grip]) + button')`;
await open("&ord=1", "exits?tab=groups", "main");
await js(`${DOWN1}.click()`);
out.flip = { anim: await js(ANIM), rows: (await js(TIERS("main")))[0].rows };
await cdp("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
await open("&ord=1", "exits?tab=groups", "main");
await js(`${DOWN1}.click()`);
out.flip.reduced = await js(ANIM);
await cdp("Emulation.setEmulatedMedia", { features: [] });

step('key');
// key
await open("&ord=1", "exits?tab=groups", "main");
await js(`${GRIP("main", "Ключ 3")}.focus()`);
await key(...SPACE); await key(...UP);
const PRESSED = `document.activeElement && document.activeElement.getAttribute('aria-pressed')`;
out.key = { lifted: await js(`!!${CARD("main")}.querySelector('.is-lifted')`), live: await js(`[...document.querySelectorAll('.gx-vh[role="status"]')].map((n) => n.textContent).filter(Boolean).pop() || ''`),
  pressed: await js(PRESSED) };
await key(...ESC);
out.key.cancel = (await js(TIERS("main")))[0].rows;
out.key.pressedEsc = await js(`${GRIP("main", "Ключ 3")}.getAttribute('aria-pressed')`);
await js(`${GRIP("main", "Ключ 3")}.focus()`);
await key(...SPACE); await key(...UP); await key(...UP); await key(...SPACE);
out.key.drop = (await js(TIERS("main")))[0];
out.key.focus = await js(`document.activeElement && document.activeElement.getAttribute('data-ord-grip')`);
out.key.pressedDrop = await js(PRESSED);

step("touch");
// палец: удержание ручки ~250 мс и ведение; вне ручки — прокрутка страницы, порядок прежний. «Группы» на телефоне (<700)
// не показываются (PAGES.exits.phone, skip groups) — «Основная» пальцем на 768 (планшет), внутренняя группа — на 390
const SCROLLED = `(document.scrollingElement || document.documentElement).scrollTop + [...document.querySelectorAll('*')].reduce((s, n) => s + (n.scrollTop || 0), 0)`;
// head — бросить на шапку шага, где стоит «to» (граница шагов: обмен); иначе — на верх строки «to»
async function touchDrag(gid, from, to, head) {
  const g = await js(AT(GRIP(gid, from)));
  const r = await js(head ? `(() => { const r = ${ROW(gid, to)}.closest('[data-ord-sec]').querySelector('[data-ord-head]').getBoundingClientRect(); return { y: r.bottom - 6 }; })()`
    : `(() => { const r = ${ROW(gid, to)}.getBoundingClientRect(); return { y: r.top + 4 }; })()`);
  await cdp("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: g.x, y: g.y }] });
  await sleep(350);
  for (let i = 1; i <= 10; i++) { await cdp("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: g.x, y: g.y + (r.y - g.y) * i / 10 }] }); await sleep(30); }
  await cdp("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(300);
}
async function touchScroll(gid, name) {
  const y0 = await js(SCROLLED);
  const n = await js(AT(`${ROW(gid, name)}.querySelector('.kk-eg-exit__n')`));
  await cdp("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: n.x, y: n.y }] });
  for (let i = 1; i <= 8; i++) { await cdp("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: n.x, y: n.y - 25 * i }] }); await sleep(25); }
  await cdp("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await sleep(400);
  return (await js(SCROLLED)) !== y0;
}
await size(768, 1024, true);
await open("&ord=1", "exits?tab=groups", "main");
await touchDrag("main", "Ключ 4", "Ключ 1");
out.touch = { tier: (await js(TIERS("main")))[0] };
out.touch.scrolled = await touchScroll("main", "Ключ 1");
out.touch.after = (await js(TIERS("main")))[0].rows;
await shot("manual-768");
await size(390, 844, true);
await open("&rug=ok", "exits?tab=ru", "g-d0d0d0");
await shot("ru-390");
out.touch390 = { before: (await js(TIERS("g-d0d0d0"))).map((s) => s.rows) };
// Ш6: строки плотнее, верх «Роутера» уже не у края окна (раньше страница подкручивалась и палец попадал в шапку шага).
// Бросить НА ключ — «в один шаг с ним» (макет В2), обмен шагов — бросить на границу шага (шапку)
await touchDrag("g-d0d0d0", "Дача", "Роутер", true);
out.touch390.after = (await js(TIERS("g-d0d0d0"))).map((s) => s.rows);
out.touch390.scrolled = await touchScroll("g-d0d0d0", "Роутер");
await shot("ru-390-after");

step('conn');
// conn
await size(1440, 900);
await open("&ord=conn", "exits?tab=groups", "main");
out.conn = { tier: (await js(TIERS("main")))[0] };
await mouseDrag("main", "Ключ 2", "Ключ 1");
out.conn.after = (await js(TIERS("main")))[0].rows;

// ── REV-order: бросок вне уровней (С-1), бросок на ключ другого шага (С-2), ручной уровень с нерабочими (С-3) ──
const SAID = `[...document.querySelectorAll('.gx-vh[role="status"]')].map((n) => n.textContent).filter(Boolean).pop() || ''`;
const LINE = (gid) => `(() => { const l = ${CARD(gid)}.querySelector('.gx-ord-line'); const row = ${CARD(gid)}.querySelector('[data-ord-row].is-lifted');
  return l ? { shown: !l.hidden, no: l.classList.contains('is-no'), zone: l.classList.contains('is-zone'), target: l.classList.contains('is-target'), text: l.textContent,
    rowNo: !!(row && row.classList.contains('is-no')), drop: [...${CARD(gid)}.querySelectorAll('[data-ord-sec].is-drop')].length,
    tgt: [...${CARD(gid)}.querySelectorAll('[data-ord-sec].is-target')].map((n) => n.dataset.ordSec) } : null; })()`;
// мышью или пальцем за ручку «from» в точку (x, y) из выражения xy (null x — тот же столбец): состояние линии перед
// отпусканием (K3_SHOT и name — ещё снимок в этот миг)
async function dragTo(gid, from, xy, touch, name) {
  const g = await js(AT(GRIP(gid, from)));
  const p = await js(xy), x = typeof p.x === "number" ? p.x : g.x;
  if (touch) { await cdp("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: g.x, y: g.y }] }); await sleep(350); }
  else { await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: g.x, y: g.y }); await cdp("Input.dispatchMouseEvent", { type: "mousePressed", x: g.x, y: g.y, button: "left", buttons: 1, clickCount: 1 }); }
  for (let i = 1; i <= 10; i++) {
    const q = { x: g.x + (x - g.x) * i / 10, y: g.y + (p.y - g.y) * i / 10 };
    if (touch) await cdp("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [q] });
    else await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: q.x, y: q.y, button: "left", buttons: 1 });
    await sleep(30);
  }
  await sleep(60);
  const line = await js(LINE(gid));
  if (name) await shot(name);
  if (touch) await cdp("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  else await cdp("Input.dispatchMouseEvent", { type: "mouseReleased", x, y: p.y, button: "left", buttons: 0, clickCount: 1 });
  await sleep(300);
  return line;
}
// точки без прокрутки страницы: верх ручки — у середины окна, цели — ниже или выше неё в пределах окна
const BELOW_ACTS = (gid) => `(() => { const a = ${CARD(gid)}.querySelector('.kk-eg-acts').getBoundingClientRect(); return { y: a.bottom + 40 }; })()`;
const ABOVE_CARD = (gid) => `(() => { const s = ${CARD(gid)}.querySelector('[data-ord-sec]').getBoundingClientRect(); return { y: s.top - 30 }; })()`;
const RIGHT_OF = (gid, name) => `(() => { const b = ${CARD(gid)}.querySelector('[data-ord-root]').getBoundingClientRect(), r = ${ROW(gid, name)}.getBoundingClientRect(); return { x: b.right + 12, y: r.top + 4 }; })()`;
const PLACE = (gid, name) => `(() => { ${ROW(gid, name)}.scrollIntoView({ block: 'center' }); return true; })()`;

step('outside');
await size(1440, 900);
out.outside = {};
await open("&ord=1", "exits?tab=groups", "main");
await js(`(() => { ${CARD("main")}.querySelector('.kk-eg-acts').scrollIntoView({ block: 'center' }); return true; })()`);
out.outside.below = { line: await dragTo("main", "Ключ 2", BELOW_ACTS("main"), false, "outside-drag-1440"), said: await js(SAID) };
out.outside.below.tiers = (await js(TIERS("main"))).map((x) => ({ rows: x.rows, method: x.method }));
out.outside.below.dirty = await js(DIRTY("main"));
await shot("outside-1440");
await js(PLACE("main", "Ключ 1"));
out.outside.above = { line: await dragTo("main", "Ключ 2", ABOVE_CARD("main")) };
out.outside.above.tiers = (await js(TIERS("main"))).map((x) => x.rows);
await js(PLACE("main", "Ключ 1"));
out.outside.right = { line: await dragTo("main", "Ключ 2", RIGHT_OF("main", "Ключ 1")) };
out.outside.right.tiers = (await js(TIERS("main"))).map((x) => x.rows);
await size(768, 1024, true);
await open("&ord=1", "exits?tab=groups", "main");
await js(`(() => { ${CARD("main")}.querySelector('.kk-eg-acts').scrollIntoView({ block: 'center' }); return true; })()`);
out.outside.touch = { line: await dragTo("main", "Ключ 4", BELOW_ACTS("main"), true) };
out.outside.touch.tiers = (await js(TIERS("main"))).map((x) => x.rows);

step('merge');
// внутренняя группа «Дом»: «Дача» на строку «Роутер» — явно в один шаг, с подписью и строкой в «Что изменится»
const AT_ROW = (gid, name, k) => `(() => { const r = ${ROW(gid, name)}.getBoundingClientRect(); return { y: r.top + r.height * ${k} }; })()`;
const IMPACT = (gid) => `(async () => { const b = [...${CARD(gid)}.querySelectorAll('.kk-sb button')].find((x) => x.getAttribute('aria-expanded') !== null);
  if (!b) return null; if (b.getAttribute('aria-expanded') !== 'true') b.click();
  for (let i = 0; i < 60; i++) { const l = [...${CARD(gid)}.querySelectorAll('.kk-sb .kk-ca__lines li')].map((x) => x.textContent); if (l.length) return l; await new Promise((ok) => setTimeout(ok, 100)); }
  return []; })()`;
await size(1440, 900);
out.merge = {};
for (const lang of ["ru", "en"]) {
  await open("&rug=ok" + (lang === "en" ? "&lang=en" : ""), "exits?tab=ru", "g-d0d0d0");
  const [ROUTER, DACHA] = lang === "en" ? ["Router", "Country house"] : ["Роутер", "Дача"];   // заглушка переводит имена
  await js(PLACE("g-d0d0d0", ROUTER));
  const m = { before: (await js(TIERS("g-d0d0d0"))).map((x) => x.rows) };
  m.line = await dragTo("g-d0d0d0", DACHA, AT_ROW("g-d0d0d0", ROUTER, 0.4), false, "merge-drag-" + lang);
  m.after = (await js(TIERS("g-d0d0d0"))).map((x) => x.rows);
  m.tgtLeft = await js(`${CARD("g-d0d0d0")}.querySelectorAll('.is-target').length`);
  m.impact = await js(IMPACT("g-d0d0d0"));
  if (lang === "ru") await shot("merge-1440");
  out.merge[lang] = m;
}

step('manualOut');
// «Запасной» (Вена, Хельсинки — ухудшен, Токио — нет ответа): «По порядку (вручную)», ↓ у «Вены», «Сохранить»; ждём ряды
// сервера по новому способу (до них — «пересчитаются после сохранения», REV-order З-1)
await open("&ord=1", "exits?tab=groups", "main");
const SEC2 = `[...${CARD("main")}.querySelectorAll('[data-ord-sec]')][1]`;
await js(`(() => { const s = ${SEC2}.querySelector('.kk-eg-tier__pw select'); s.value = 'manual'; s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`);
await sleep(200);
const MO = `(() => { const s = ${SEC2}; return { rows: [...s.querySelectorAll('[data-ord-row]')].map((r) => r.querySelector('.kk-eg-exit__n > b').textContent),
  tags: [...s.querySelectorAll('[data-ord-row] .kk-eg-rk')].map((x) => x.textContent), outh: s.querySelectorAll('.kk-eg-outh').length,
  was: [...s.querySelectorAll('[data-ord-row] .kk-eg-exit__n .gx-desc')].map((x) => x.textContent).filter((x) => /было/.test(x)),
  dirty: s.querySelectorAll('[data-ord-row].is-dirty').length, chips: s.querySelectorAll('[data-ord-row] .kk-eg-st').length,
  outs: s.querySelectorAll('[data-ord-row].is-out').length,
  note: (s.querySelector('.kk-eg-rkn') || {}).textContent || '' }; })()`;
out.manualOut = { draft: await js(MO) };
const VENA_DOWN = `${ROW("main", "Вена")}.querySelector('.kk-eg-ord > button:not([data-ord-grip]) + button')`;
await js(`${VENA_DOWN}.click()`);
await sleep(200);
out.manualOut.moved = await js(MO);
await js(`(() => { const b = [...${CARD("main")}.querySelectorAll('.kk-sb button')].find((x) => x.textContent.trim() === 'Сохранить' && !x.disabled); if (b) b.click(); return !!b; })()`);
const POL2 = `window.KASKAD_MOCK.db().policy.groups.find((g) => g.id === 'main').tiers[1]`;
await until(`${POL2}.order === 'manual'`, 10000).catch(() => null);
out.manualOut.saved = await js(`(() => { const t = ${POL2}; return { order: t.order || null, n: t.exits.length }; })()`);
// ряды сервера «вручную» пришли: полоса сохранения пуста, строки «не участвует» отмечены по рядам (is-out). Строка под
// шапкой здесь не признак: у ручного уровня её нет и до прихода рядов
out.manualOut.fresh = await until(`${DIRTY("main")} === '' && (${MO}).outs > 0`, 30000).then(() => true, () => false);
out.manualOut.after = await js(MO);
out.manualOut.dirtyBar = await js(DIRTY("main"));
await shot("manual-out-1440");

step('steps');
// steps — внутренняя группа «Дом»
await open("&rug=ok", "exits?tab=ru", "g-d0d0d0");
out.steps = { before: (await js(TIERS("g-d0d0d0"))).map((s) => s.rows) };
await js(`(() => { const b = ${ROW("g-d0d0d0", "Дача")}.querySelector('.kk-eg-ord > button:not([data-ord-grip])'); b.click(); })()`);
await sleep(300);
out.steps.after = (await js(TIERS("g-d0d0d0"))).map((s) => s.rows);

step('stepWas');
// Н-2 стенда 3.3.7.13: внутренняя группа «Дом», «Роутер» и «Дача» в одном шаге (rug=tier, «Лучший отклик», ряд Р1):
// ↑ у второго ключа — ход внутри шага: шаг «По порядку (вручную)», у ключей «было: Р1», как в обычной группе (макет П3)
const SW = (gid) => `(() => { const s = ${CARD(gid)}.querySelector('[data-ord-sec]'); return { rows: [...s.querySelectorAll('[data-ord-row]')].map((r) => r.querySelector('.kk-eg-exit__n > b').textContent),
  tags: [...s.querySelectorAll('[data-ord-row] .kk-eg-rk')].map((x) => x.textContent),
  was: [...s.querySelectorAll('[data-ord-row] .kk-eg-exit__n .gx-desc')].map((x) => x.textContent.split(' · ')[0]).filter((x) => /было/.test(x)),
  dirty: s.querySelectorAll('[data-ord-row].is-dirty').length }; })()`;
const UP2 = (gid) => `(() => { const r = [...${CARD(gid)}.querySelectorAll('[data-ord-sec]')][0].querySelectorAll('[data-ord-row]')[1]; const b = r && r.querySelector('.kk-eg-ord > button:not([data-ord-grip])'); if (!b || b.disabled) return false; b.click(); return true; })()`;
await size(1440, 900);
await open("&rug=tier", "exits?tab=ru", "g-d0d0d0");
out.stepWas = { before: await js(SW("g-d0d0d0")) };
out.stepWas.clicked = await js(UP2("g-d0d0d0"));
await until(`${SW("g-d0d0d0")}.rows[0] === 'Дача'`, 5000).catch(() => null);
out.stepWas.after = await js(SW("g-d0d0d0"));

step('bar390');
// М-2 стенда 3.3.7.13: телефон, полоса правок (макет «Порядок» Т1–Т4, gx-msbar): «Изменений: N · Что изменится» строкой,
// под ней что изменится, кнопки — последней строкой поровну; пустой полосы между счётчиком и кнопками нет
const BAR = (gid) => `(() => { const b = ${CARD(gid)}.querySelector('.kk-sb'); if (!b || b.hidden) return null; const R = (n) => (n ? n.getBoundingClientRect() : null);
  const btn = (re) => [...b.querySelectorAll('button')].find((x) => re.test(x.textContent.trim()));
  const n = R(b.querySelector('.kk-sb__n')), w = R(b.querySelector('button[aria-expanded]')), c = R(btn(/^Отменить$/)), s = R(btn(/^Сохранить$/)), l = R(b.querySelector('.kk-ca__lines'));
  return { nTop: n.top, nBot: n.bottom, wTop: w.top, wBot: w.bottom, cTop: c.top, cBot: c.bottom, sTop: s.top, cW: c.width, sW: s.width, lTop: l ? l.top : null, lBot: l ? l.bottom : null,
    bar: R(b).width, over: b.scrollWidth > b.clientWidth + 1 }; })()`;
await size(390, 844, true);
await open("&rug=tier", "exits?tab=ru", "g-d0d0d0");
out.bar390 = { clicked: await js(UP2("g-d0d0d0")) };
await until(`${DIRTY("g-d0d0d0")} !== ''`, 5000).catch(() => null);
out.bar390.closed = await js(BAR("g-d0d0d0"));
out.bar390.lines = await js(IMPACT("g-d0d0d0"));                 // ждёт строк «Что изменится»
out.bar390.open = await js(BAR("g-d0d0d0"));
await shot("bar-390");
out.errors = ERR;
console.log(JSON.stringify(out));
process.exit(0);
