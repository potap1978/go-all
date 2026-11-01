#!/usr/bin/env node
// k3_writes_check.mjs — одновременные записи панели на макете (?mock=1) в настоящем Chrome (этап 1, T8; SE §7 «Версия
// политики», таблица «Одновременные записи — сценарии», D-095). Каждый сценарий — своя вкладка «Выходы → Группы» или
// деталь выхода; «другое место» (вторая вкладка, бот, SSH) — прямой запрос к макету KASKAD_MOCK.fetch с актуальной rev.
// Сценарии (строка = проверка):
//   form-silent   правка формы группы; другое место меняет не её объект (rev растёт) — «Сохранить» → 409 → перекладка
//                 молча, «Данные изменились — пересчитал», сохранение новым нажатием проходит (правка на сервере);
//   form-list     другое место меняет объект формы — список «Группу … изменили, пока вы правили», «Применить снова»
//                 накладывает правку на свежие данные, «Сохранить» сохраняет обе;
//   button-409    подтверждение «В запас»: rev сменилась между прогоном и нажатием — новый прогон, «пересчитал»,
//                 действие — новым нажатием;
//   anyway        прогон не удался (dry=fail) — «Не удалось посчитать», «Всё равно …» проходит с rev чтения;
//   x-anyway-409  лист выхода: «Всё равно …» → 409 → перечитать — раскрытое подтверждение остаётся (не сносится
//                 пересборкой детали), «обновите и повторите», второе «Всё равно» проходит (REV-s1-t8m-3 З-2);
//   busy          сервер занят (busy=1, код 6) — «Сервер применяет предыдущее изменение…» и «Повторить», повтор сохраняет;
//   gone          ключ удалили в другом месте — «Удалить ключ» на плитке говорит «Уже удалено», без ошибки;
//   dirty-block   у группы несохранённые правки — её выходы в окне «Добавить выход…» другой группы неактивны,
//                 «Добавить выход…» своей группы неактивна;
//   leave         несохранённые правки — переход на вкладку «Выходы» спрашивает «Сохранить правки?» (Сохранить / Не
//                 сохранять / Остаться, D-077): «Остаться» — правки на месте, «Не сохранять» — ушли, ничего не записано;
//   leave-save    уход через адрес с правками — «Сохранить» записывает правку и уходит;
//   leave-save-fail, leave-save-fail-speed  «Сохранить» в вопросе не удалось (список после 409, красное поле) при свёрнутой
//                 карточке — человек остаётся, карточка раскрыта, полоса с причиной и тост «Не сохранено» (REV-s1-t8m З-2);
//   leave-save-two  «Сохранить» в вопросе с правками двух групп — записаны обе (REV-s1-t8m З-5);
//   leave-back    «Назад» браузера на другую вкладку раздела с правками — тот же вопрос, «Остаться» возвращает адрес;
//   settings-leave «Уведомления»: уход из «Настроек» с правкой — вопрос; повторный вход читает заново (REV-s1-t8-a З-2);
//   tiers-max     «+ Уровень» неактивна на 10-м уровне (policy.MAX_TIERS);
//   detail-move   деталь выхода: правка квоты выключает «Перенести в группу…»; в окне переноса группа-цель, у которой
//                 появились правки, выключает «Перенести»;
//   trans-more    «Показать ещё» в истории переходов листает дальше, без повторов;
//   hint-keep     «Оставить как раньше» — только флаг плашки: rev политики не растёт, плашка уходит;
//   std-dirty     «Включить «Стандарт»» неактивна при правках группы — на карточке и в открытом окне (REV-s1-t8m З-5);
//   tab-ask       кнопка вкладки раздела с правками — вопрос с тремя кнопками; закрытие страницы — окно браузера;
//   group-focus   «Открыть группу» из карточки устройства — вкладка «Группы» с раскрытой и подсвеченной группой;
//   group-focus-phone  то же на телефоне (390): вкладки «Группы» там нет — список выходов, не «Этого выхода больше нет»;
//   del-phone     телефон (390): «Удалить ключ» на экране выхода — назад к списку и тост «удалён», а не пустой экран
//                 «Этого выхода больше нет» (T-3377 Н-14);
//   route-groups  форма маршрута устройства с группами (§7.3): «другие выходы группы», «как у группы», запись;
//   noexit        «Без интернета: N устройство» в сводке «Обзора» → список «Без выхода»; жёлтые «выход удалён» и «идёт напрямую»;
//   rule-form     форма условия (§7.2): ошибка под полем держит «Сохранить», исправленное сохраняется;
//   rule-manual   «Только вручную» откладывает условия возврата: ошибка уходит, записан возврат без условий;
//   speed-month-cap  прогноз теста скорости группы «≈ N ГБ в месяц» — не больше бюджета в месяц;
//   dns-line      Р-Г9: у «Основной» при «Блок» — строка про российские сайты по имени (SPEC-stage1-exit-groups §5);
//                 UI-A2: одной строкой + «Подробнее», раскрытое переживает пересборку карточки;
//   groups-fold   UI-A2: группы свёрнуты строкой, Enter / пробел, фокус, перерисовка не схлопывает, одна акцентная
//                 кнопка, «Проверять страну» со значением;
//   quota-form    квота выхода: полоса формы, кнопки «сразу» выключены, вопрос при закрытии листа, запись с rev;
//                 строка 21: имя сменили во время правки квоты — квота сохраняется без 409;
//   quota-409     квоту сменили в другом месте, пока правили, — 409 и список «Настройки выхода … изменили» прямо из ответа
//                 на 409, без прогона «пересчитал» (мелочи после 3.3.7.12 п. 5: гонка перекладки с перерисовкой);
//   default-route «Маршрут по умолчанию»: вопрос «напрямую», прогон, чужая правка — «Применить снова», запись с ver;
//   route-409     строка 19: маршрут устройства изменил бот — 409, «изменили, пока вы правили», чужая запись на месте;
//   speed-409     строка 25: общий тест скорости в двух вкладках — 409 и список, чужая запись не потеряна;
//   rename-dup    имя выхода, как у другого — «Оставить такое же?» и повтор с allow_duplicate (REV-s1-t7 З-4);
//   rename-route  T13: устройство переименовали в другом месте во время правки маршрута — «Применить» без 409;
//   rename-device-dup  T13: имя другого устройства человека — «Оставить такое же?», счётчик знаков, логин прежний;
//   rv-speed-revisit*, rv-notify-revisit*, notify-rebuild  повторный вход на вкладку и пересборка раздела: полоса
//                 «Сохранить» формы-одиночки рисует текущую карточку — 409, «Отменить», следующая правка (REV-s1-t8-2 С-1);
//   rv-route-inherit-409, user-route-inherit-409  409 маршрута после «Как у человека…»/«Как по умолчанию» — «Ваш
//                 выбор» словами, без «inherit» (REV-s1-t8-2 З-1).
// Запуск: node tools/k3_writes_check.mjs [сценарии через запятую]. Нужны Node ≥ 22 (WebSocket) и Chrome; без них — SKIP (77).
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, mkdtemp } from "node:fs/promises";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, extname, dirname, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "lib", "kaskad", "panel", "static");
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
const CHROMES = [process.env.CHROME, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome",
  "/usr/bin/chromium", "/usr/bin/chromium-browser"].filter(Boolean);
const CHROME = CHROMES.find((p) => existsSync(p));
if (!CHROME || typeof WebSocket === "undefined") { console.log("SKIP: нет Chrome или Node без WebSocket — записи панели не проверены"); process.exit(77); }
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".json": "application/json" };
const srv = createServer(async (req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  if (p.startsWith("/static/")) p = p.slice(7);
  const f = normalize(join(ROOT, p === "/" ? "index.html" : p));
  if (!f.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
  try { const b = await readFile(f); res.writeHead(200, { "content-type": TYPES[extname(f)] || "application/octet-stream" }); res.end(b); }
  catch (e) { res.writeHead(404); res.end(); }
});
await new Promise((ok) => srv.listen(0, "127.0.0.1", ok));
const BASE = `http://127.0.0.1:${srv.address().port}/index.html`;

// R-333 повтор №7: порт отладки выбирает сам Chrome (0 → свободный) и пишет его в DevToolsActivePort профиля — к чужому
// браузеру на занятом порту набор не подключится. Под root на Linux Chrome без --no-sandbox не стартует.
const prof = await mkdtemp(join(tmpdir(), "k3-writes-"));
const root = typeof process.getuid === "function" && process.getuid() === 0;
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", ...(root ? ["--no-sandbox"] : []), "about:blank"], { stdio: ["ignore", "ignore", "pipe"], detached: true });
let chromeErr = "", chromeGone = null;
chrome.stderr.on("data", (b) => { chromeErr = (chromeErr + b).slice(-2000); });
chrome.on("error", (e) => { chromeGone = e.message; });
chrome.on("exit", (code, sig) => { chromeGone = chromeGone || "код " + code + (sig ? ", " + sig : ""); });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } srv.close(); try { rmSync(prof, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch (e) { /* останется в $TMPDIR */ } };  // REV-speed-2b С3: и профиль Chrome
process.on("exit", stop);
let wsUrl = null;
for (let i = 0; i < 75 && !wsUrl && !chromeGone; i++) {
  try {
    const port = Number((await readFile(join(prof, "DevToolsActivePort"), "utf8")).split("\n")[0]);
    const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) wsUrl = p.webSocketDebuggerUrl;
  } catch (e) { /* ещё поднимается */ }
  if (!wsUrl) await sleep(200);
}
// Chrome не поднялся — это среда, а не вёрстка: SKIP с причиной (как без Chrome), k3_test.sh не краснеет
if (!wsUrl) {
  const why = chromeGone ? "Chrome завершился (" + chromeGone + ")" : "Chrome не ответил за 15 с";
  const tail = chromeErr.trim().split("\n").filter(Boolean).slice(-1)[0] || "";
  console.log("SKIP: " + why + (tail ? ": " + tail.slice(0, 200) : "") + " — записи панели не проверены"); process.exit(77);
}
const ws = new WebSocket(wsUrl);
await new Promise((ok) => ws.addEventListener("open", ok));
let seq = 0; const wait = new Map();
// ошибки страницы — находка того вида, на котором случились
const jsErrs = [];
const onEvent = (m) => {
  if (m.method === "Runtime.exceptionThrown") { const d = m.params.exceptionDetails || {}; jsErrs.push(((d.exception && d.exception.description) || d.text || "exception").split("\n").slice(0, 2).join(" | ")); }
  else if (m.method === "Runtime.consoleAPICalled" && (m.params.type === "error" || m.params.type === "assert")) jsErrs.push("console." + m.params.type + ": " + (m.params.args || []).map((a) => a.value !== undefined ? String(a.value) : (a.description || a.type)).join(" ").slice(0, 300));
};
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } else if (m.method) onEvent(m); });
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; wait.set(id, (m) => (m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result))); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
const until = async (expr, ms) => { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* страница грузится */ } await sleep(150); } return false; };

// ── помощники в странице ──────────────────────────────────────────────────────────────────────────────────────────
const H = `(() => {
  const vis = (e) => !!e && !e.closest('[hidden]') && e.getClientRects().length > 0;
  window.W = {
    btn: (txt, root) => [...(root || document).querySelectorAll('button')].filter((b) => vis(b) && b.textContent.trim() === txt).pop() || null,
    card: (label) => document.querySelector('.kk-eg-card[aria-label="' + label + '"]'),
    // UI-A2: группы свёрнуты строкой — раскрыть (кнопка с именем, aria-expanded), если свёрнута; готово — видна форма
    openCard: (label) => { const c = W.card(label); if (!c) return false; const b = c.querySelector('.kk-eg-tog[aria-expanded="false"]'); if (b) b.click(); return !!c.querySelector('.kk-eg-row select'); },
    // второй уровень группы «Поведение и проверки» (UI-A2) — раскрыть, если свёрнут
    openMore: (label) => { const c = W.card(label); const b = c && c.querySelector('.kk-eg-more__tog[aria-expanded="false"]'); if (b) b.click(); return !!c && !!c.querySelector('.kk-eg-more__b:not([hidden]) .kk-eg-freq'); },
    text: (root) => (root || document.body).innerText,
    mock: async (m, p, b) => { const r = await window.KASKAD_MOCK.fetch(p, { method: m, headers: { 'X-Kaskad-Admin': '1' }, body: b ? JSON.stringify(b) : undefined }); return [r.status, await r.json()]; },
    rev: async () => (await W.mock('GET', '/api/gw/policy'))[1].rev,
    setSelect: (s, v) => { s.value = v; s.dispatchEvent(new Event('change')); },
  };
  return true;
})()`;
async function open(qs, hash) {
  // сначала пустая страница: переход, который меняет только «#…», страницу не перезагружает — макет помнил бы прошлый сценарий
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}?mock=1&lat=0${qs ? "&" + qs : ""}#${hash}` });
  await sleep(300);
  if (!(await until(`!!window.KaskadPanel && !!document.querySelector('.gx-page:not([hidden])')`, 8000))) throw new Error("панель не открылась");
  await js(H);
}
const step = async (expr, ms = 6000, what = expr) => { if (!(await until(expr, ms))) throw new Error("не дождался: " + what.slice(0, 160)); };
const click = (txt, root = "") => js(`(() => { const b = W.btn(${JSON.stringify(txt)}${root ? ", " + root : ""}); if (!b || b.disabled) return false; b.click(); return true; })()`);
const mustClick = async (txt, root = "") => { if (!(await click(txt, root))) throw new Error("нет активной кнопки «" + txt + "»"); };
const mainCard = `W.card('Основная')`;
const failSel = (card) => `${card}.querySelector('.kk-eg-row select')`;
const groupsReady = `W.openCard('Основная')`;
const DLG = "document.querySelector('[role=alertdialog]')";
// nav2 r1: вкладок «Выходы · Группы» нет — части раздела в подменю слева (ссылки #exits?tab=…)
const exTab = (name) => js(`[...document.querySelectorAll('nav[data-subnav="exits"] a.gx-subnav__i')].find((b) => b.querySelector('.gx-subnav__l').textContent.trim() === ${JSON.stringify(name)}).click(); true`);

const SCENARIOS = {
  async "form-silent"() {
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`W.text(${mainCard}).includes('Изменений: 1')`);
    // другое место: настройка выхода (не объект формы группы) — rev растёт
    await js(`(async () => W.mock('PUT', '/api/gw/exits/e-9aa1b2c3d4', { speed_skip: true, rev: await W.rev() }))()`);
    await mustClick("Сохранить", mainCard);
    await step(`W.text(${mainCard}).includes('Данные изменились — пересчитал')`);
    await step(`!!W.btn('Сохранить', ${mainCard}) && !W.btn('Сохранить', ${mainCard}).disabled`);
    await mustClick("Сохранить", mainCard);
    await step(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups[0].fail === 'direct')()`, 6000, "fail = direct на сервере");
  },
  async "form-list"() {
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`W.text(${mainCard}).includes('Изменений: 1')`);
    await js(`(async () => { const p = (await W.mock('GET', '/api/gw/policy'))[1]; const doc = p.policy; doc.groups[0].return_to_primary.hold_min = 20; return W.mock('PUT', '/api/gw/policy', { policy: doc, rev: p.rev }); })()`);
    await mustClick("Сохранить", mainCard);
    await step(`W.text(${mainCard}).includes('Группу «Основная» изменили, пока вы правили')`);
    await mustClick("Применить снова", mainCard);
    await step(`W.text(${mainCard}).includes('Изменений: 1')`);
    await mustClick("Сохранить", mainCard);
    await step(`(async () => { const g = (await W.mock('GET', '/api/gw/policy'))[1].policy.groups[0]; return g.fail === 'direct' && g.return_to_primary.hold_min === 20; })()`, 6000, "обе правки на сервере");
  },
  async "button-409"() {
    await open("", "exits?focus=e-41a19b9cbe&open=exit"); await step(`!!W.btn('В запас')`);
    await mustClick("В запас");
    await step(`W.text(document.querySelector('.gx-sheet')).includes('Переедут устройства')`);
    await js(`(async () => W.mock('PUT', '/api/gw/exits/e-9aa1b2c3d4', { speed_skip: true, rev: await W.rev() }))()`);
    await mustClick("В запас", "document.querySelector('.kk-ca:not([hidden])')");
    await step(`W.text(document.querySelector('.gx-sheet')).includes('Данные изменились — пересчитал')`);
    await mustClick("В запас", "document.querySelector('.kk-ca:not([hidden])')");
    await step(`(async () => (await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-41a19b9cbe').health === 'standby')()`, 6000, "выход в запасе");
  },
  async anyway() {
    await open("dry=fail", "exits?focus=e-41a19b9cbe&open=exit"); await step(`!!W.btn('В запас')`);
    await mustClick("В запас");
    await step(`W.text(document.querySelector('.gx-sheet')).includes('Не удалось посчитать последствия')`);
    await mustClick("Всё равно в запас");
    await step(`(async () => (await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-41a19b9cbe').health === 'standby')()`, 6000, "выход в запасе");
  },
  async "x-anyway-409"() {
    // REV-s1-t8m-3 З-2: лист выхода, прогон не удался, чужая правка, «Всё равно в запас» → 409 → перечитать (новая rev,
    // деталь пересобирается) — раскрытое подтверждение остаётся: «обновите и повторите», второе «Всё равно» проходит
    await open("dry=fail&sse=0", "exits?focus=e-41a19b9cbe&open=exit"); await step(`!!W.btn('В запас')`);
    const ca = "document.querySelector('.gx-sheet .kk-ca.is-confirm')";
    await mustClick("В запас");
    await step(`W.text(document.querySelector('.gx-sheet')).includes('Не удалось посчитать последствия')`);
    await js(`(async () => W.mock('PUT', '/api/gw/exits/e-9aa1b2c3d4', { speed_skip: true, rev: await W.rev() }))()`);
    await mustClick("Всё равно в запас", ca);
    await step(`!!${ca} && W.text(${ca}).includes('Данные изменились — обновите и повторите')`, 6000, "подтверждение с «обновите и повторите»");
    await sleep(1500);                                   // деталь пересобирается (rev, отклик) — подтверждение на месте
    await step(`!!${ca} && !!W.btn('Всё равно в запас', ${ca})`, 3000, "«Всё равно» снова в раскрытом подтверждении");
    if (await js(`(async () => (await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-41a19b9cbe').health === 'standby')()`)) throw new Error("ушло без второго нажатия");
    await mustClick("Всё равно в запас", ca);
    await step(`(async () => (await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-41a19b9cbe').health === 'standby')()`, 6000, "выход в запасе");
    // закрыли — ряд отпущен: следующая сборка детали — новые кнопки, без висящих подтверждений
    await step(`!${ca}`, 4000, "подтверждение закрылось");
    await step(`document.querySelectorAll('.gx-sheet [data-hold-row^="eg-detail:"]').length === 1 && document.querySelectorAll('.gx-sheet .kk-ca.is-confirm').length === 0`, 4000, "один ряд действий");
  },
  async busy() {
    await open("busy=1", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await mustClick("Сохранить", mainCard);
    await step(`W.text(${mainCard}).includes('Сервер применяет предыдущее изменение')`);
    await mustClick("Повторить", mainCard);
    await step(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups[0].fail === 'direct')()`, 6000, "сохранено после повтора");
  },
  async gone() {
    // UI-A2: «Все выходы» — таблица; корзина выхода — в раскрытой строке (data-hold-row — у строки с раскрытием)
    await open("", "exits"); await step(`!!document.querySelector('[data-hold-row="exit:e-0f0f0f0f0f"]')`);
    await js(`document.querySelector('[data-hold-row="exit:e-0f0f0f0f0f"] .gx-xt__x').click(); true`);
    // подтверждение открыто и посчитано, ключ удалили в другом месте, затем — «Удалить ключ»
    await js(`document.querySelector('[data-hold-row="exit:e-0f0f0f0f0f"] .kk-ca__btn[aria-label*="Новый ключ"]').click(); true`);
    await step(`!!W.btn('Удалить ключ', document.querySelector('.kk-ca.is-confirm')) && !W.btn('Удалить ключ', document.querySelector('.kk-ca.is-confirm')).disabled`);
    await js(`(async () => W.mock('DELETE', '/api/gw/exits/e-0f0f0f0f0f?rev=' + await W.rev()))()`);
    await mustClick("Удалить ключ", "document.querySelector('.kk-ca.is-confirm')");
    await step(`W.text().includes('Уже удалено')`);
    if (await js(`!!document.querySelector('.gx-toast--danger, .gx-al--danger[role=alert]')`)) throw new Error("ошибка вместо «Уже удалено»");
  },
  async "dirty-block"() {
    await open("", "exits?tab=groups"); await step(`${groupsReady} && !!W.card('Стриминг')`);
    await step(`W.openCard('Стриминг')`);
    await js(`W.setSelect(W.card('Стриминг').querySelector('.kk-eg-row select'), 'direct'); true`);
    await step(`!!document.querySelector('[data-eg-add="g-5f1a2b"]') && document.querySelector('[data-eg-add="g-5f1a2b"]').disabled`, 4000, "«Добавить выход…» своей группы неактивна");
    await js(`document.querySelector('[data-eg-add="main"]').click(); true`);
    await step(`[...document.querySelectorAll('[role=radio]')].some((b) => b.textContent.includes('Новый ключ') && b.getAttribute('aria-disabled') === 'true')`, 4000, "выход группы с правками неактивен");
  },
  async leave() {
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await exTab("Все выходы");
    await step(`W.text().includes('Сохранить правки?')`);
    if (!(await js(`location.hash.includes('tab=groups')`))) throw new Error("ушли без вопроса");
    await mustClick("Остаться", DLG);
    await step(`!document.querySelector('[role=alertdialog]') && W.text(${mainCard}).includes('Изменений: 1')`, 3000, "остались с правками");
    await exTab("Все выходы");
    await step(`W.text().includes('Сохранить правки?')`);
    await mustClick("Не сохранять", DLG);
    await step(`!location.hash.includes('tab=groups') && !!document.querySelector('.gx-tc')`, 4000, "ушли на список");
    if (await js(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups[0].fail === 'direct')()`)) throw new Error("«Не сохранять» записало правку");
  },
  async "leave-save"() {
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await js(`location.hash = '#overview'; true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос при уходе через адрес");
    const back = await js(`location.hash`);
    if (back !== "#exits?tab=groups") throw new Error("адрес на время вопроса: " + back);
    await mustClick("Остаться", DLG);
    await sleep(300);
    if ((await js(`location.hash`)) !== "#exits?tab=groups") throw new Error("после «Остаться» адрес " + (await js(`location.hash`)));
    // «Остаться» — адрес на экране прежний; следующий уход снова спрашивает и «Сохранить» уходит туда, куда просили
    await js(`location.hash = '#overview'; true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос при втором уходе");
    await mustClick("Сохранить", DLG);
    await step(`location.hash.indexOf('#overview') === 0`, 6000, "ушли после записи");
    await step(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups[0].fail === 'direct')()`, 6000, "правка записана");
  },
  async "leave-save-fail"() {
    // «Сохранить» в вопросе не удалось (чужая правка той же группы — список), карточка была свёрнута: человек остаётся,
    // карточка раскрыта, видна полоса со списком «Применить снова» и тост «Не сохранено…» (REV-s1-t8m З-2)
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`W.text(${mainCard}).includes('Изменений: 1')`);
    await js(`${mainCard}.querySelector('.kk-eg-tog').click(); true`);
    await step(`!${mainCard}.querySelector('.kk-eg-row select')`, 3000, "свёрнута");
    await js(`(async () => { const p = (await W.mock('GET', '/api/gw/policy'))[1]; const doc = p.policy; doc.groups[0].fail = 'block'; doc.groups[0].return_to_primary.hold_min = 20; return W.mock('PUT', '/api/gw/policy', { policy: doc, rev: p.rev }); })()`);
    await js(`location.hash = '#overview'; true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос");
    await mustClick("Сохранить", DLG);
    await step(`!${DLG} && !!${mainCard}.querySelector('.kk-eg-row select') && !!W.btn('Применить снова', ${mainCard})`, 6000, "карточка раскрыта, список виден");
    await step(`W.text(document.querySelector('#toastHost')).includes('Не сохранено')`, 3000, "тост «Не сохранено»");
    if ((await js(`location.hash`)) !== "#exits?tab=groups") throw new Error("ушли: " + (await js(`location.hash`)));
  },
  async "leave-save-fail-speed"() {
    // «Тест скорости — общее»: красное поле, карточка свёрнута — «Сохранить» в вопросе ничего не шлёт, карточка
    // раскрыта с полосой
    await open("", "settings?tab=health");          // круг 1 Т1: тест скорости — «Настройки › Проверка сервера»
    const card = `document.querySelector('[data-find="settings.speedtest"]')`;
    await step(`!!${card} && !!${card}.querySelector('input')`, 8000, "карточка раскрыта сразу");
    const v0 = await js(`(async () => (await W.mock('GET', '/api/gw/probes/speed'))[1].ver)()`);
    await js(`(() => { const i = ${card}.querySelectorAll('input.gx-input')[0]; i.value = '999'; i.dispatchEvent(new Event('input')); return true; })()`);
    await js(`[...${card}.querySelectorAll('button')].find((b) => b.getAttribute('aria-expanded') === 'true').click(); true`);
    await step(`!${card}.querySelector('input')`, 3000, "свёрнута");
    await js(`location.hash = '#people'; true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос");
    await mustClick("Сохранить", DLG);
    await step(`!${DLG} && !!${card}.querySelector('input') && W.text(${card}).includes('Изменений') && !!W.btn('Сохранить', ${card})`, 6000, "карточка раскрыта, полоса видна");
    if ((await js(`location.hash`)) !== "#settings?tab=health") throw new Error("ушли: " + (await js(`location.hash`)));
    if ((await js(`(async () => (await W.mock('GET', '/api/gw/probes/speed'))[1].ver)()`)) !== v0) throw new Error("записано с красным полем");
  },
  async "leave-save-two"() {
    // «Сохранить» в вопросе: правки двух групп — обе записаны, вторая перекладывается на свежую rev (REV-s1-t8m З-5, U7)
    await open("", "exits?tab=groups"); await step(`${groupsReady} && !!W.card('Стриминг')`);
    await step(`W.openCard('Стриминг')`);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); W.setSelect(W.card('Стриминг').querySelector('.kk-eg-row select'), 'direct'); true`);
    await sleep(300);
    await js(`location.hash = '#overview'; true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос");
    await mustClick("Сохранить", DLG);
    await step(`location.hash.indexOf('#overview') === 0`, 8000, "ушли после записи");
    await step(`(async () => { const g = (await W.mock('GET', '/api/gw/policy'))[1].policy.groups; return g[0].fail === 'direct' && g.find((x) => x.id === 'g-5f1a2b').fail === 'direct'; })()`, 6000, "обе группы записаны");
  },
  async "leave-back"() {
    await open("", "exits"); await step(`!!document.querySelector('.gx-tc')`);
    await js(`location.hash = '#exits?tab=groups'; true`); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await js(`history.back(); true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос на «Назад»");
    await step(`location.hash.includes('tab=groups')`, 2000, "адрес вкладки вернулся");
    await mustClick("Остаться", DLG);
    await step(`W.text(${mainCard}).includes('Изменений: 1')`, 3000, "правки на месте");
  },
  async "settings-leave"() {
    await open("", "settings?tab=notify");          // UI-5: «Уведомления» — своя часть «Настроек»
    const card = `document.querySelector('[data-find="settings.notify"]')`;
    // Н8 (UI-A1): подпись вида — в строке li.gx-nrow рядом с переключателем (label for), у самой кнопки текста нет
    const flap = `[...${card}.querySelectorAll('[role=switch]')].find((b) => (b.closest('li') || b).textContent.includes('Сводка «нестабилен»'))`;
    await step(`!!${card} && !!${flap}`, 6000, "карточка «Уведомления»");
    const was = await js(`${flap}.getAttribute('aria-checked')`);
    await js(`${flap}.click(); true`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 3000, "полоса уведомлений");
    // закрытие страницы с правкой «Уведомлений» — окно браузера (общий обработчик ядра; REV-s1-t8m З-5, U4)
    if (!(await js(`(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; })()`))) throw new Error("закрытие страницы с правкой не спрашивает");
    await js(`location.hash = '#overview'; true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос при уходе из «Настроек»");
    await mustClick("Не сохранять", DLG);
    await step(`location.hash.indexOf('#overview') === 0`, 4000);
    // пока нас не было, «Уведомления» поменяли в другом месте — повторный вход показывает свежее, а не первое чтение
    await js(`(async () => { const n = (await W.mock('GET', '/api/notify'))[1]; return W.mock('PUT', '/api/notify', { flap_summary: ${JSON.stringify(was)} !== 'true', ver: n.ver }); })()`);
    await js(`location.hash = '#settings?tab=notify'; true`);
    await step(`!!${card} && !!${flap} && ${flap}.getAttribute('aria-checked') === ${JSON.stringify(was === "true" ? "false" : "true")} && !W.text(${card}).includes('Изменений')`, 6000, "свежие данные при повторном входе");
  },
  async "tiers-max"() {
    await open("", "exits?tab=groups"); await step(groupsReady);
    for (let i = 0; i < 12; i++) { if (!(await click("Уровень", mainCard))) break; await sleep(60); }
    await step(`W.card('Основная').querySelectorAll('.kk-eg-tier').length === 10`, 3000, "10 уровней");
    await step(`(() => { const b = [...W.card('Основная').querySelectorAll('button')].find((x) => x.textContent.trim() === 'Уровень'); return !!b && b.disabled; })()`, 3000, "«+ Уровень» неактивна на 10-м");
  },
  async "detail-move"() {
    // деталь поверх вкладки «Группы»: карточки групп под листом, их правки видны окну переноса
    await open("", "exits?tab=groups&focus=e-41a19b9cbe&open=exit");
    const sheet = `document.querySelector('.gx-sheet')`, dlg = `document.querySelector('.gx-dlg[role=dialog]')`;
    await step(`${groupsReady} && W.openCard('Стриминг') && !!document.querySelector('[data-eg-xform] .kk-eg-num') && !!W.btn('Перенести в группу…', ${sheet})`, 8000, "вкладка и деталь");
    await js(`(() => { const n = document.querySelector('[data-eg-xform] .kk-eg-num'); n.value = '60'; n.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.btn('Перенести в группу…', ${sheet}).disabled`, 3000, "«Перенести в группу…» неактивна при правке квоты");
    await mustClick("Отменить", `document.querySelector('[data-eg-xform]')`);
    await step(`!W.btn('Перенести в группу…', ${sheet}).disabled`, 3000, "снова активна после «Отменить»");
    await mustClick("Перенести в группу…", sheet);
    await step(`!!${dlg} && [...${dlg}.querySelectorAll('[role=radio]')].some((b) => b.textContent.includes('Стриминг'))`, 4000, "окно переноса");
    await js(`[...${dlg}.querySelectorAll('[role=radio]')].find((b) => b.textContent.includes('Стриминг')).click(); true`);
    await step(`!!W.btn('Перенести', ${dlg}) && !W.btn('Перенести', ${dlg}).disabled`, 3000, "«Перенести» активна");
    // у группы-цели появились правки (её карточка под окном) — «Перенести» выключается
    await js(`W.setSelect(W.card('Стриминг').querySelector('.kk-eg-row select'), 'direct'); true`);
    await step(`!!${dlg} && W.btn('Перенести', ${dlg}).disabled`, 3000, "«Перенести» неактивна: у группы-цели правки");
  },
  async "trans-more"() {
    await open("trans=many", "exits?focus=e-41a19b9cbe&open=exit"); await step(`document.querySelectorAll('.kk-eg-trans li').length === 20`, 8000, "первая страница истории");
    await mustClick("Показать ещё");
    await step(`document.querySelectorAll('.kk-eg-trans li').length === 40`, 4000, "вторая страница");
    const ts = await js(`[...document.querySelectorAll('.kk-eg-trans__t')].map((x) => x.textContent)`);
    if (new Set(ts).size !== ts.length) throw new Error("«Показать ещё» повторил начало: " + ts.length + " строк, разных " + new Set(ts).size);
  },
  async "hint-keep"() {
    await open("", "exits?tab=groups"); await step(`!!W.btn('Оставить как раньше')`, 6000, "плашка");
    const r0 = await js(`W.rev()`);
    await mustClick("Оставить как раньше");
    await step(`!W.btn('Оставить как раньше')`, 4000, "плашка ушла");
    const p = await js(`(async () => (await W.mock('GET', '/api/gw/policy'))[1])()`);
    if (!p.policy.hint_seen) throw new Error("hint_seen не записан");
    if (p.rev !== r0) throw new Error("rev политики вырос: закрытие плашки — запись политики");
  },
  async "std-dirty"() {
    // «Включить «Стандарт»» (плашка и карточка) неактивна при правках группы, и в уже открытом окне — тоже (REV-s1-t8m З-5,
    // U5, U6); «Отменить» — снова активна
    await open("", "exits?tab=groups"); await step(`${groupsReady} && !!document.querySelector('[data-eg-std="main"]')`, 6000, "кнопка «Стандарт»");
    const std = `[...document.querySelectorAll('[data-eg-std="main"]')]`;
    if (!(await js(`${std}.every((b) => !b.disabled)`))) throw new Error("«Стандарт» неактивна без правок");
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`${std}.every((b) => b.disabled && b.title === 'Сначала сохраните или отмените правки')`, 3000, "«Стандарт» неактивна при правках");
    await mustClick("Отменить", mainCard);
    await step(`${std}.every((b) => !b.disabled)`, 3000, "«Стандарт» снова активна");
    // окно уже открыто, правка появилась (форма за окном) — кнопка в окне неактивна
    await js(`${std}[0].click(); true`);
    const dlg = "[...document.querySelectorAll('.gx-dlg, [role=dialog]:not(.gx-sheet)')].pop()";
    await step(`!!${dlg} && !!W.btn('Включить «Стандарт»', ${dlg}) && !W.btn('Включить «Стандарт»', ${dlg}).disabled`, 4000, "окно «Стандарт»");
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`[...${dlg}.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'Включить «Стандарт»').every((b) => b.disabled)`, 3000, "кнопки в окне неактивны при правках");
  },
  async "tab-ask"() {
    // переход на другую вкладку раздела кнопкой вкладки с правками — вопрос с тремя кнопками (REV-s1-t8m З-5, U3);
    // закрытие страницы с правками — окно браузера (U4)
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`W.text(${mainCard}).includes('Изменений: 1')`);
    const unload = `(() => { const e = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented; })()`;
    if (!(await js(unload))) throw new Error("закрытие страницы с правками не спрашивает");
    await exTab("Все выходы");
    await step(`!!${DLG} && W.text(${DLG}).includes('Сохранить правки?')`, 4000, "вопрос на вкладке");
    for (const b of ["Сохранить", "Не сохранять", "Остаться"]) if (!(await js(`!!W.btn(${JSON.stringify(b)}, ${DLG})`))) throw new Error("в вопросе нет «" + b + "»");
    await mustClick("Остаться", DLG);
    await step(`!${DLG} && W.text(${mainCard}).includes('Изменений: 1')`, 3000, "правки на месте");
    await mustClick("Отменить", mainCard);
    await sleep(300);
    if (await js(unload)) throw new Error("без правок закрытие страницы спрашивает");
  },
  async "group-focus"() {
    await open("", "people?focus=d-3c3c0003&open=settings");
    await step(`!!W.btn('Открыть группу')`, 6000, "кнопка «Открыть группу»");
    await mustClick("Открыть группу");
    await step(`location.hash.includes('tab=groups') && /focus=(main|g-[0-9a-f]{6})/.test(location.hash)`, 4000, "адрес с группой");
    await step(`(() => { const c = document.querySelector('.kk-eg-card.is-found'); return !!c && !!c.querySelector('.kk-eg-row select'); })()`, 6000, "группа раскрыта и подсвечена");
  },
  async "group-focus-phone"() {
    // REV-337-final З-1: на телефоне (390) вкладки «Группы» нет — «Открыть группу» и адрес «tab=groups&focus=<группа>»
    // ведут на список выходов, а не на деталь выхода с id группы («Этого выхода больше нет»)
    await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    try {
      const listed = `(async () => { const ex = (await W.mock('GET', '/api/gw/exits'))[1].exits.filter((e) => e.name); return document.documentElement.dataset.device === 'phone' && !!W.btn('Ключ') && ex.length > 0 && ex.every((e) => W.text().includes(e.name)); })()`;
      await open("", "people?focus=d-3c3c0003&open=settings");
      await step(`document.documentElement.dataset.device === 'phone' && !!W.btn('Открыть группу')`, 8000, "телефон, кнопка «Открыть группу»");
      await mustClick("Открыть группу");
      await step(`location.hash.startsWith('#exits')`, 4000, "переход на «Выходы»");
      await step(listed, 8000, "список выходов");
      if (await js(`W.text().includes('Этого выхода больше нет')`)) throw new Error("«Этого выхода больше нет» вместо списка");
      await open("", "exits?tab=groups&focus=main");                      // тот же адрес прямой загрузкой
      await step(listed, 8000, "список выходов по адресу");
      if (await js(`W.text().includes('Этого выхода больше нет')`)) throw new Error("адрес: «Этого выхода больше нет»");
      const ex0 = await js(`(async () => { const e = (await W.mock('GET', '/api/gw/exits'))[1].exits.find((x) => x.name); return [e.id, e.name]; })()`);
      await open("", "exits?focus=" + ex0[0]);                            // деталь выхода на телефоне — как была
      await step(`W.text().includes(${JSON.stringify(ex0[1])}) && !W.text().includes('Этого выхода больше нет') && !W.btn('Ключ')`, 8000, "деталь выхода (без кнопки «Ключ» в шапке)");
    } finally {
      await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    }
  },
  async "del-phone"() {
    await cdp("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    try {
      const ca = `document.querySelector('.kk-ca__btn[aria-label*="Новый ключ"]')`, ask = "document.querySelector('.kk-ca.is-confirm')";
      await open("", "exits?focus=e-0f0f0f0f0f");
      await step(`document.documentElement.dataset.device === 'phone' && !!${ca}`, 8000, "экран выхода с корзиной");
      await js(`${ca}.click(); true`);
      await step(`!!W.btn('Удалить ключ', ${ask}) && !W.btn('Удалить ключ', ${ask}).disabled`, 6000, "подтверждение посчитано");
      await mustClick("Удалить ключ", ask);
      await step(`location.hash.startsWith('#exits') && !/focus=/.test(location.hash)`, 6000, "назад к списку выходов");
      await step(`W.text(document.querySelector('#toastHost')).includes('удалён')`, 4000, "тост «удалён»");
      await sleep(600);
      if (await js(`W.text().includes('Этого выхода больше нет')`)) throw new Error("«Этого выхода больше нет» после удаления");
    } finally {
      await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    }
  },
  // ── часть 2 (T8): маршрут с группами, «нет выхода», имя-дубль ───────────────────────────────────────────────────
  async "route-groups"() {
    // форма маршрута устройства (SE §7.3): группы вместо «пула», у выхода — «другие выходы группы, затем …»; запись с ver
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`;
    await step(`!!${sel} && [...${sel}.options].some((o) => o.textContent === 'Группа «Стриминг»') && [...${sel}.options].some((o) => o.textContent === 'Группа «Основная»')`, 6000, "группы в выборе маршрута");
    if (await js(`[...${sel}.options].some((o) => o.textContent === 'Пул выходов')`)) throw new Error("«Пул выходов» при группах");
    await js(`W.setSelect(${sel}, 'exit:e-41a19b9cbe'); true`);
    await step(`W.text(document.querySelector('.gx-sheet')).includes('другие выходы группы «Основная», затем')`, 3000, "строка «другие выходы группы»");
    await js(`W.setSelect(${sel}, 'group:g-5f1a2b'); true`);
    await step(`W.text(document.querySelector('.gx-sheet')).includes('как у группы')`, 3000, "строка «как у группы»");
    await mustClick("Применить", "document.querySelector('.gx-sheet')");
    await step(`!!W.btn('Применить', document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)'))`, 4000, "сводка маршрута");
    await mustClick("Применить", "document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)')");
    // «как у группы» — fail «block» явно: у узла по умолчанию «при отказе — напрямую», без fail сервер (и макет — как
    // model.kept_fail) записал бы унаследованный «напрямую» (REV-s1-t8-b С-1)
    await step(`(async () => { const r = (await W.mock('GET', '/api/gw/people'))[1].devices.find((d) => d.id === 'd-7ae67ae0').route || {}; return r.via === 'group:g-5f1a2b' && r.fail === 'block'; })()`, 6000, "маршрут на сервере — группа «Стриминг», при отказе — как у группы (block)");
  },
  async "route-fail"() {
    // выход из «как у человека» при «напрямую» у узла: «другие выходы группы, затем — как у группы», записано «block»;
    // «Напрямую» — только своим выбором
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`;
    await step(`!!${sel}`, 6000, "форма маршрута");
    await js(`W.setSelect(${sel}, 'exit:e-41a19b9cbe'); true`);
    await step(`document.querySelector('.gx-sheet .kk-route__then select').value === 'block'`, 3000, "«как у группы» по умолчанию");
    await mustClick("Применить", "document.querySelector('.gx-sheet')");
    await mustClick("Применить", "document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)')");
    await step(`(async () => { const r = (await W.mock('GET', '/api/gw/people'))[1].devices.find((d) => d.id === 'd-7ae67ae0').route || {}; return r.via === 'exit:e-41a19b9cbe' && r.fail === 'block'; })()`, 6000, "записано «block»");
  },
  async noexit() {
    // «Обзор»: «Нет выхода: N» → отфильтрованный список; пометки у устройств; пауза — не сбой
    await open("", "overview");
    await step(`W.text().includes('Без интернета: 1 устройство') && !!document.querySelector('[data-ovs="noexit"] button')`, 8000, "счётчик на «Обзоре»");
    await js(`document.querySelector('[data-ovs="noexit"] button').click(); true`);   // круг 1 Т1: замечание «Сервер сейчас» (Сводка Б)
    // UI-4 круг 2: пометки — короткими метками, полный текст — в подсказке; Т3 (вариант Б): метка — у имени строки
    // (.gx-xt__nl), колонки «Состояние» больше нет (REV-it1-b С-3)
    const tips = `[...document.querySelectorAll('.gx-page:not([hidden]) .gx-xt__nl .gx-chip')].map((c) => c.textContent + '|' + c.title).join('\\n')`;
    await step(`location.hash.indexOf('#people') === 0 && W.text().includes('socks-g3') && ${tips}.includes('нет выхода|нет выхода в интернет')`, 6000, "отфильтрованный список");
    if (await js(`W.text(document.querySelector('.gx-page:not([hidden])')).includes('Телефон Мамы')`)) throw new Error("фильтр «Без выхода» показал устройство с выходом");
    // UI-4: у фильтров — числа («Все 4»); nav2 r1 (UI-A1): фильтры — кнопки подменю «Людей», первая — «Все»
    await step(`location.hash === '#people?show=noexit'`, 4000, "фильтр «Без выхода» в адресе");
    await js(`document.querySelector('nav[data-subnav="people"] button.gx-subnav__i').click(), true`);
    await js(`(document.querySelectorAll('.gx-xt__x[aria-expanded=false]').forEach((b) => b.click()), true)`);
    await step(`${tips}.includes('выбранный выход удалён:') && ${tips}.includes('идёт напрямую|выходы недоступны — идёт с сервера напрямую')`, 6000, "жёлтые пометки");
  },
  async "rule-form"() {
    // форма условия (SE §7.2): возврат не по ту сторону порога — ошибка под полем и «Сохранить» неактивна; исправили —
    // сохраняется в политику группы
    await open("", "exits?tab=groups&open=rule"); await step(`!!document.querySelector('.kk-eg-rform')`, 10000, "форма условия");
    const card = `document.querySelector('.kk-eg-rform').closest('.kk-eg-card')`;
    const setNum = (i, v) => js(`(() => { const n = document.querySelectorAll('.kk-eg-rform .kk-eg-num')[${i}]; n.value = ${JSON.stringify(v)}; n.dispatchEvent(new Event('input')); return true; })()`);
    await setNum(2, "3");
    await step(`W.text(document.querySelector('.kk-eg-rform')).includes('Порог возврата') && !!W.btn('Сохранить', ${card}) && W.btn('Сохранить', ${card}).disabled`, 4000, "ошибка и неактивная «Сохранить»");
    await setNum(2, "1.2");
    await step(`!W.text(document.querySelector('.kk-eg-rform')).includes('Порог возврата') && !W.btn('Сохранить', ${card}).disabled`, 4000, "ошибка ушла");
    await mustClick("Сохранить", card);
    await step(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups.some((g) => g.rules.some((r) => r.back && r.back.conds[0].value === 1.2)))()`, 6000, "условие сохранено");
  },
  async "rule-manual"() {
    // «Только вручную» (REV-s1-t8-2 П-3, X9): условия возврата откладываются — ошибка порога уходит, в политику уходит
    // возврат без условий; выключили — условия вернулись
    await open("", "exits?tab=groups&open=rule"); await step(`!!document.querySelector('.kk-eg-rform')`, 10000, "форма условия");
    const form = `document.querySelector('.kk-eg-rform')`, card = `${form}.closest('.kk-eg-card')`;
    const sw = `[...${form}.querySelectorAll('button[role=switch]')].find((b) => b.textContent.startsWith('Только вручную'))`;
    await js(`(() => { const n = ${form}.querySelectorAll('.kk-eg-num')[2]; n.value = '3'; n.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.text(${form}).includes('Порог возврата') && W.btn('Сохранить', ${card}).disabled`, 4000, "ошибка порога");
    await js(`${sw}.click(); true`);
    await step(`!W.text(${form}).includes('Порог возврата') && !!W.btn('Сохранить', ${card}) && !W.btn('Сохранить', ${card}).disabled`, 4000, "«Только вручную»: ошибка ушла");
    await js(`${sw}.click(); true`);
    await step(`W.text(${form}).includes('Порог возврата')`, 4000, "выключили — условия (с ошибкой) вернулись");
    await js(`${sw}.click(); true`);
    await mustClick("Сохранить", card);
    await step(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups.some((g) => g.rules.some((r) => r.back && r.back.manual === true && r.back.conds.length === 0 && !r.back.hold)))()`, 6000, "записан возврат «только вручную» без условий");
  },
  async "speed-month-cap"() {
    // прогноз «≈ N ГБ в месяц» теста скорости группы не больше бюджета в месяц сервера (REV-s1-t8m П-1)
    await open("", "exits?tab=groups"); await step(groupsReady); await step(`W.openMore('Основная')`, 3000, "«Поведение и проверки»");
    const sw = `[...${mainCard}.querySelectorAll('button[role=switch]')].find((b) => b.textContent.startsWith('Тест скорости группы'))`;
    await js(`${sw}.click(); true`);
    const every = `${mainCard}.querySelector('select[aria-label="Как часто"]')`;
    await step(`!!${every}`, 3000, "«Как часто»");
    await js(`W.setSelect(${every}, String(Math.min(...[...${every}.options].map((o) => Number(o.value))))); true`);
    const budget = await js(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.probes.speed.budget_month_gb)()`);
    await step(`/≈ [\\d,.]+ ГБ в месяц на тесты скорости этой группы/.test(W.text(${mainCard}))`, 3000, "прогноз");
    const line = await js(`W.text(${mainCard}).split('\\n').find((l) => l.includes('ГБ в месяц на тесты скорости'))`);
    const n = Number((/≈ ([\d,.]+) ГБ/.exec(line) || [])[1].replace(",", "."));
    if (!(n <= budget) || !line.includes("упрётся в бюджет")) throw new Error("прогноз " + line + " при бюджете " + budget + " ГБ");
  },
  async "dns-line"() {
    // Р-Г9: строка «российские сайты не откроются…» у «Основной» при «Блок»; «Напрямую» — строки нет. Без «по имени»:
    // без выходов и при «напрямую» у соседа закрыт и адрес (REV-338 З-1)
    // UI-A2: одна строка + «Подробнее» (полный текст раскрыт — и после пересборки карточки)
    await open("", "exits?tab=groups"); await step(groupsReady);
    const short = `W.text(${mainCard}).includes('при «Блок» и недоступных выходах не откроются и российские сайты')`;
    const has = `W.text(${mainCard}).includes('российские сайты не откроются — даже у пользователей с «Россия напрямую»')`;
    await js(`W.setSelect(${failSel(mainCard)}, 'block'); true`);
    await step(`${short} && !${has}`, 3000, "строка при «Блок», полный текст свёрнут");
    await mustClick("Подробнее", mainCard);
    await step(has, 3000, "полный текст по «Подробнее»");
    await step(`W.openMore('Основная')`, 3000, "пересборка карточки");
    await step(has, 3000, "полный текст раскрыт после пересборки");
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`!${has} && !${short}`, 3000, "без строки при «Напрямую»");
  },
  async "groups-fold"() {
    // UI-A2: группы свёрнуты строкой; Enter / пробел раскрывают и сворачивают, фокус остаётся на кнопке; живая
    // перерисовка (данные выходов сменились) не схлопывает раскрытое; одна акцентная кнопка на экран; «Проверять страну»
    // показывает своё значение целиком
    await open("", "exits?tab=groups"); await step(`!!W.card('Основная') && !!W.card('Стриминг')`, 6000, "группы");
    if (!(await js(`[...document.querySelectorAll('.kk-eg-tog')].every((b) => b.getAttribute('aria-expanded') === 'false') && !document.querySelector('.kk-eg-row select')`))) throw new Error("группы не свёрнуты");
    const acc = `[...document.querySelectorAll('.kk-part .ga-btn--primary')].filter((b) => b.getClientRects().length).map((b) => b.textContent.trim())`;
    const a0 = await js(acc);
    if (a0.length !== 1 || a0[0] !== "Включить «Стандарт»") throw new Error("акцентные кнопки: " + JSON.stringify(a0));
    const key = async (k) => { const o = k === "Enter" ? { key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r" } : { key: " ", code: "Space", windowsVirtualKeyCode: 32, text: " " };
      await cdp("Input.dispatchKeyEvent", Object.assign({ type: "keyDown" }, o)); await cdp("Input.dispatchKeyEvent", Object.assign({ type: "keyUp" }, o)); };
    await js(`${mainCard}.querySelector('.kk-eg-tog').focus(); true`);
    await key("Enter");
    await step(`${mainCard}.querySelector('.kk-eg-tog').getAttribute('aria-expanded') === 'true' && !!${failSel(mainCard)} && document.activeElement === ${mainCard}.querySelector('.kk-eg-tog')`, 3000, "Enter раскрыл, фокус на кнопке");
    await key(" ");
    await step(`${mainCard}.querySelector('.kk-eg-tog').getAttribute('aria-expanded') === 'false' && !${failSel(mainCard)} && document.activeElement === ${mainCard}.querySelector('.kk-eg-tog')`, 3000, "пробел свернул");
    await step(`W.openCard('Основная') && W.openMore('Основная')`, 3000, "раскрыта с «Поведением и проверками»");
    const geo = `${mainCard}.querySelector('select[aria-label="Проверять страну"]')`;
    const fits = await js(`(() => { const s = ${geo}; const c = document.createElement('canvas').getContext('2d'); const cs = getComputedStyle(s); c.font = cs.font; const w = c.measureText(s.selectedOptions[0].text).width;
      return s.value !== '' && s.selectedOptions[0].text.length > 5 && s.clientWidth >= w + parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight) - 1; })()`);
    if (!fits) throw new Error("«Проверять страну»: значение не видно целиком");
    // живая перерисовка: выход переименовали в другом месте — данные перечитаны, раскрытое на месте
    await js(`(async () => W.mock('PUT', '/api/gw/exits/e-41a19b9cbe', { name: 'Вена-2' }))()`);
    await js(`window.KaskadPanel.loadAll(['exits', 'policy']); true`);
    await step(`W.text(${mainCard}).includes('Вена-2')`, 6000, "перечитано");
    if (!(await js(`${mainCard}.querySelector('.kk-eg-tog').getAttribute('aria-expanded') === 'true' && !!${mainCard}.querySelector('.kk-eg-more__b:not([hidden]) .kk-eg-freq')`))) throw new Error("перерисовка схлопнула группу");
    // правка в раскрытой группе — акцент у «Сохранить» её полосы, «Включить «Стандарт»» и «+ Группа» — обычные
    await js(`W.setSelect(${failSel(mainCard)}, 'direct'); true`);
    await step(`(() => { const a = ${acc}; return a.length === 1 && a[0] === 'Сохранить'; })()`, 3000, "одна акцентная — «Сохранить»");
    // клавиши выше дали странице «действие человека»: с правкой следующий переход открыл бы окно «Покинуть сайт?» и
    // повис — правку отменить
    await mustClick("Отменить", mainCard);
    await step(`!W.text(${mainCard}).includes('Изменений') && (() => { const a = ${acc}; return a.length === 1 && a[0] === 'Включить «Стандарт»'; })()`, 3000, "правка отменена, акцент снова у «Стандарта»");
  },
  async "quota-form"() {
    // квота выхода — своя полоса с ?dry=1 (SE §7.3): кнопки «сразу» над выходом выключены, закрытие листа спрашивает,
    // «Сохранить» пишет квоту с rev
    await open("", "exits?focus=e-41a19b9cbe&open=exit"); await step(`!!document.querySelector('[data-eg-xform] .kk-eg-num')`, 8000, "форма квоты");
    const form = `document.querySelector('[data-eg-xform]')`;
    await js(`(() => { const n = ${form}.querySelectorAll('.kk-eg-num')[0]; n.value = '60'; n.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.text(${form}).includes('Изменений: 1')`, 4000, "полоса формы");
    await step(`!!W.btn('В запас', document.querySelector('.gx-sheet')) && W.btn('В запас', document.querySelector('.gx-sheet')).disabled`, 4000, "«В запас» неактивна при правках");
    await js(`document.querySelector('.gx-sheet .gx-close').click(); true`);
    await step(`W.text().includes('Сохранить правки?')`, 4000, "вопрос при закрытии листа");
    await mustClick("Остаться", DLG);
    await step(`!!document.querySelector('.gx-sheet [data-eg-xform]')`, 3000, "лист остался");
    // строка 21: имя выхода сменили, пока правили квоту, — имя вне отпечатка: квота сохраняется с той же rev, без 409
    await js(`W.mock('PUT', '/api/gw/exits/e-41a19b9cbe', { name: 'Вена-2' })`);
    await mustClick("Сохранить", form);
    await step(`(async () => ((await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-41a19b9cbe').quota || {}).limit_gb === 60)()`, 6000, "квота сохранена");
    if (await js(`W.text(${form}).includes('изменили, пока вы правили')`)) throw new Error("имя дало перекладку квоты");
    await step(`!W.btn('В запас', document.querySelector('.gx-sheet')).disabled`, 4000, "кнопки снова активны");
  },
  async "quota-409"() {
    await open("", "exits?focus=e-41a19b9cbe&open=exit"); await step(`!!document.querySelector('[data-eg-xform] .kk-eg-num')`, 8000, "форма квоты");
    const form = `document.querySelector('[data-eg-xform]')`;
    await js(`(() => { const n = ${form}.querySelectorAll('.kk-eg-num')[0]; n.value = '60'; n.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.text(${form}).includes('Изменений: 1')`, 4000, "полоса формы");
    // другое место (вторая вкладка) пишет квоту 70 со своей свежей rev — у формы rev устарела
    await js(`(async () => { const ex = (await W.mock('GET', '/api/gw/exits'))[1], q = ex.exits.find((e) => e.id === 'e-41a19b9cbe').quota;
      return W.mock('PUT', '/api/gw/exits/e-41a19b9cbe', { quota: { period: q.period, limit_gb: 70, reset_day: q.reset_day, warn_pct: q.warn_pct, action: q.action }, rev: ex.rev }); })()`);
    await js(`(() => { const m = window.KASKAD_MOCK, f = m.fetch; W.q409 = 0; W.qDry = 0;
      m.fetch = async (u, i) => { const meth = (i && i.method) || 'GET', put = meth === 'PUT' && String(u).includes('/api/gw/exits/e-41a19b9cbe');
        if (W.q409 > 0 && put && String(u).includes('dry=1')) W.qDry++;
        const r = await f.call(m, u, i);
        if (r.status === 409 && put) W.q409++;
        return r; };
      return true; })()`);
    await mustClick("Сохранить", form);
    await step(`W.q409 > 0`, 30000, "ответ 409 на запись квоты");
    await step(`W.text(${form}).includes('изменили, пока вы правили')`, 30000, "список «Применить снова»");
    const dries = await js(`W.qDry`);
    if (dries !== 0) throw new Error("после 409 был прогон «пересчитал» (" + dries + "): список не из ответа на 409");
    await mustClick("Применить снова", form);
    await step(`!!W.btn('Сохранить', ${form}) && !W.btn('Сохранить', ${form}).disabled`, 4000);
    await mustClick("Сохранить", form);
    await step(`(async () => ((await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-41a19b9cbe').quota || {}).limit_gb === 60)()`, 6000, "записана своя квота 60");
  },
  async "default-route"() {
    // «Маршрут по умолчанию» — своя полоса (SE §7.9 З-4): «напрямую» — вопрос при выборе; другое место меняет маршрут —
    // «изменили, пока вы правили» и «Применить снова»; запись — с ver
    await open("", "exits?tab=default"); const card = `document.querySelector('.kk-route-wide').closest('.gx-card, section, .gx-sec')`;   // nav2 r1: своя часть
    const sel = `document.querySelector('.kk-route-wide select')`;
    await step(`!!${sel}`, 6000, "карточка маршрута");
    await js(`W.setSelect(${sel}, 'direct'); true`);
    await step(`W.text().includes('Пускать напрямую?')`, 4000, "вопрос «напрямую»");
    await mustClick("Выбрать «напрямую»");
    await step(`W.text(${card}).includes('Изменений: 1')`, 4000, "полоса маршрута");
    await mustClick("Что изменится", card);
    await step(`W.text(${card}).includes('Внимание: трафик пойдёт с адреса узла')`, 6000, "строки прогона");
    await js(`(async () => { const ex = (await W.mock('GET', '/api/gw/exits'))[1]; return W.mock('PUT', '/api/gw/route', { via: 'block', fail: 'block', ver: ex.default_route_ver }); })()`);
    // REV-337-final З-2: ждать по признаку — ответ 409 на запись панели пришёл, — а не 6 с на всё (под нагрузкой мало).
    // REV-337-last З-1: 409 приходит через миллисекунды, медленная часть — перечитывание после него (onConflict →
    // loadAll(["exits"]) → перерисовка): ждём ответ GET /api/gw/exits, начатый после 409, и текст — с тем же запасом
    // мелочи после 3.3.7.12 п. 5: список — прямо из ответа на 409 (onConflict), без прогона «пересчитал»: прогон после
    // 409 значит, что список собрался лишь следующей перерисовкой, и поздний ответ прогона мог его стереть (гонка -j4)
    await js(`(() => { const m = window.KASKAD_MOCK, f = m.fetch; W.route409 = 0; W.exitsAfter409 = 0; W.dryAfter409 = 0;
      m.fetch = async (u, i) => { const after = W.route409 > 0;
        const meth = (i && i.method) || 'GET', put = meth === 'PUT' && String(u).includes('/api/gw/route');
        if (after && put && String(u).includes('dry=1')) W.dryAfter409++;
        const r = await f.call(m, u, i);
        if (r.status === 409 && put) W.route409++;
        else if (after && meth === 'GET' && String(u).split('?')[0].endsWith('/api/gw/exits')) W.exitsAfter409++;
        return r; };
      return true; })()`);
    await mustClick("Сохранить", card);
    await step(`W.route409 > 0`, 30000, "ответ 409 на запись маршрута");
    await step(`W.exitsAfter409 > 0`, 30000, "перечитывание выходов после 409");
    await step(`W.text(${card}).includes('Маршрут по умолчанию изменили, пока вы правили')`, 30000, "список «Применить снова»");
    const dries = await js(`W.dryAfter409`);
    if (dries !== 0) throw new Error("после 409 был прогон «пересчитал» (" + dries + "): список не из ответа на 409");
    await mustClick("Применить снова", card);
    await step(`!!W.btn('Сохранить', ${card}) && !W.btn('Сохранить', ${card}).disabled`, 4000);
    await mustClick("Сохранить", card);
    await step(`(async () => (await W.mock('GET', '/api/gw/exits'))[1].default_route.via === 'direct')()`, 6000, "маршрут на сервере — напрямую");
  },
  async "route-409"() {
    // строка 19: маршрут устройства меняет бот (ver другой) — «Применить» → 409 object_changed → «изменили, пока вы
    // правили», ничего не записано, карточка — со свежим маршрутом
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`;
    await step(`!!${sel}`, 6000, "форма маршрута");
    await js(`W.setSelect(${sel}, 'group:g-5f1a2b'); true`);
    await js(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === 'd-7ae67ae0'); return W.mock('PUT', '/api/gw/devices/d-7ae67ae0/route', { via: 'block', fail: 'block', ver: d.route_ver }); })()`);
    await mustClick("Применить", "document.querySelector('.gx-sheet')");
    await mustClick("Применить", "document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)')");
    await step(`W.text().includes('изменили, пока вы правили: ничего не записано')`, 6000, "текст 409");
    await step(`(async () => ((await W.mock('GET', '/api/gw/people'))[1].devices.find((d) => d.id === 'd-7ae67ae0').route || {}).via === 'block')()`, 4000, "запись бота на месте");
  },
  async "route-409-slow"() {
    // строка 19, с паузой: бот меняет маршрут устройства, лист перечитан (SSE) — выбор человека НЕ заменён молча;
    // «Применить» → 409 → «изменили, пока вы правили», свежий маршрут, «Ваш выбор» и «Применить снова» → записан выбор
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`, sheet = "document.querySelector('.gx-sheet')";
    const dlg = "document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)')";
    await step(`!!${sel}`, 6000, "форма маршрута");
    await js(`W.setSelect(${sel}, 'group:g-5f1a2b'); true`);
    await js(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === 'd-7ae67ae0'); return W.mock('PUT', '/api/gw/devices/d-7ae67ae0/route', { via: 'block', fail: 'block', ver: d.route_ver }); })()`);
    await sleep(1500);
    if ((await js(`${sel}.value`)) !== "group:g-5f1a2b") throw new Error("выбор заменён молча: " + (await js(`${sel}.value`)));
    await mustClick("Применить", sheet);
    await mustClick("Применить", dlg);
    await step(`W.text().includes('изменили, пока вы правили: ничего не записано')`, 6000, "текст 409");
    await step(`(async () => ((await W.mock('GET', '/api/gw/people'))[1].devices.find((d) => d.id === 'd-7ae67ae0').route || {}).via === 'block')()`, 4000, "запись бота на месте");
    await step(`W.text(${sheet}).includes('Ваш выбор: ') && ${sel}.value === 'block'`, 6000, "свежий маршрут и «Ваш выбор»");
    await mustClick("Применить снова", sheet);
    await step(`${sel}.value === 'group:g-5f1a2b'`, 3000, "выбор вернулся");
    await mustClick("Применить", sheet);
    await mustClick("Применить", dlg);
    await step(`(async () => ((await W.mock('GET', '/api/gw/people'))[1].devices.find((d) => d.id === 'd-7ae67ae0').route || {}).via === 'group:g-5f1a2b')()`, 6000, "записан выбор пользователя");
  },
  async "user-route-409"() {
    // строка 19 для маршрута человека: бот поменял маршрут, пока окно открыто — 409, окно заново: свежий маршрут,
    // «Ваш выбор» и «Применить снова»
    await open("", "people?focus=u-1a2b3c4d&open=route");
    const dlg = "[...document.querySelectorAll('.gx-dlg, [role=dialog]:not(.gx-sheet)')].pop()";   // верхнее окно
    const sel = `${dlg}.querySelector('.kk-route select')`;
    await step(`!!${dlg} && !!${sel}`, 6000, "окно маршрута пользователя");
    await js(`W.setSelect(${sel}, 'group:g-5f1a2b'); true`);
    await js(`(async () => { const u = (await W.mock('GET', '/api/gw/people'))[1].users.find((x) => x.id === 'u-1a2b3c4d'); return W.mock('PUT', '/api/gw/users/u-1a2b3c4d/route', { via: 'block', fail: 'block', ver: u.route_ver }); })()`);
    await mustClick("Применить", dlg);
    await mustClick("Применить", dlg);                     // сводка
    await step(`W.text().includes('изменили, пока вы правили: ничего не записано')`, 6000, "текст 409");
    await step(`!!${dlg} && W.text(${dlg}).includes('Ваш выбор: ') && ${sel}.value === 'block'`, 6000, "окно заново со свежим маршрутом");
    await mustClick("Применить снова", dlg);
    await step(`!!${sel} && ${sel}.value === 'group:g-5f1a2b'`, 3000, "выбор вернулся");
    await mustClick("Применить", dlg);
    await mustClick("Применить", dlg);
    await step(`(async () => ((await W.mock('GET', '/api/gw/people'))[1].users.find((u) => u.id === 'u-1a2b3c4d').route || {}).via === 'group:g-5f1a2b')()`, 6000, "записан выбор пользователя");
  },
  async "user-route-inherit-409"() {
    // маршрут человека: свой → «Как по умолчанию»; бот поменял маршрут — 409, «Ваш выбор» словами (REV-s1-t8-2 З-1)
    await open("", "people?focus=u-1a2b3c4d&open=route");
    const dlg = "[...document.querySelectorAll('.gx-dlg, [role=dialog]:not(.gx-sheet)')].pop()";
    const sel = `${dlg}.querySelector('.kk-route select')`;
    const put = (via) => `(async () => { const u = (await W.mock('GET', '/api/gw/people'))[1].users.find((x) => x.id === 'u-1a2b3c4d'); return W.mock('PUT', '/api/gw/users/u-1a2b3c4d/route', { via: '${via}', fail: 'block', ver: u.route_ver }); })()`;
    await step(`!!${dlg} && !!${sel}`, 6000, "окно маршрута пользователя");
    await js(put("group:g-5f1a2b"));
    await mustClick("Отмена", dlg); await sleep(300);
    await js(`location.hash = '#people?focus=u-1a2b3c4d&open=route&n=2'; true`);
    await step(`!!${dlg} && !!${sel} && ${sel}.value === 'group:g-5f1a2b'`, 6000, "свой маршрут на экране");
    await js(`W.setSelect(${sel}, 'inherit'); true`);
    await js(put("block"));
    await mustClick("Применить", dlg);
    await mustClick("Применить", dlg);
    await step(`!!${dlg} && W.text(${dlg}).includes('Ваш выбор: ')`, 6000, "«Ваш выбор»");
    const line = await js(`W.text(${dlg}).split('\\n').find((l) => l.includes('Ваш выбор: '))`);
    if (/inherit/.test(line) || !/умолчанию/.test(line)) throw new Error("строка: " + line);
  },
  async "route-keep"() {
    // запись о ДРУГОМ устройстве (перечитывание «Людей») выбор в листе не сбрасывает
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`;
    await step(`!!${sel}`, 6000, "форма маршрута");
    await js(`W.setSelect(${sel}, 'group:g-5f1a2b'); true`);
    await js(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === 'd-0a1b2c3d'); return W.mock('PUT', '/api/gw/devices/d-0a1b2c3d/route', { via: 'block', fail: 'block', ver: d.route_ver }); })()`);
    await sleep(2500);
    if ((await js(`${sel}.value`)) !== "group:g-5f1a2b") throw new Error("выбор сброшен: " + (await js(`${sel}.value`)));
    if (!(await js(`!!W.btn('Применить', document.querySelector('.gx-sheet'))`))) throw new Error("«Применить» неактивна после перечитывания");
  },
  async "speed-409"() {
    // строка 25: общий тест скорости в двух вкладках — «Сохранить» с V0 → 409 → «Тест скорости изменили…» и список
    await open("", "settings?tab=health");          // круг 1 Т1: тест скорости — «Настройки › Проверка сервера»
    const card = `document.querySelector('[data-find="settings.speedtest"]')`;
    await step(`!!${card}.querySelector('input')`, 6000, "карточка теста скорости");
    await js(`(() => { const i = ${card}.querySelectorAll('input.gx-input')[2]; i.value = '50'; i.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 4000);
    await js(`(async () => { const sp = (await W.mock('GET', '/api/gw/probes/speed'))[1]; return W.mock('PUT', '/api/gw/probes/speed', { budget_day_mb: 30, ver: sp.ver }); })()`);
    await mustClick("Сохранить", card);
    await step(`W.text(${card}).includes('Тест скорости изменили')`, 6000, "список «Тест скорости изменили»");
    if (await js(`(async () => (await W.mock('GET', '/api/gw/probes/speed'))[1].budget_day_mb !== 30)()`)) throw new Error("чужая запись потеряна");
    // свежие данные на экране, своя правка — «Применить снова» (а не молча поверх)
    await step(`${card}.querySelectorAll('input.gx-input')[2].value === '30'`, 3000, "на экране свежее значение");
    await mustClick("Применить снова", card);
    await step(`${card}.querySelectorAll('input.gx-input')[2].value === '50'`, 3000, "своя правка вернулась");
    await mustClick("Сохранить", card);
    await step(`(async () => (await W.mock('GET', '/api/gw/probes/speed'))[1].budget_day_mb === 50)()`, 6000, "записана своя правка");
    // чужая правка ДРУГОГО поля — молча: своя остаётся, «Сохранить» ещё раз пишет обе
    await js(`(() => { const i = ${card}.querySelectorAll('input.gx-input')[0]; i.value = '20'; i.dispatchEvent(new Event('input')); return true; })()`);
    await js(`(async () => { const sp = (await W.mock('GET', '/api/gw/probes/speed'))[1]; return W.mock('PUT', '/api/gw/probes/speed', { max_s: 12, ver: sp.ver }); })()`);
    await mustClick("Сохранить", card);
    await step(`W.text(${card}).includes('ваши правки на месте')`, 6000, "«ваши правки на месте»");
    await mustClick("Сохранить", card);
    await step(`(async () => { const sp = (await W.mock('GET', '/api/gw/probes/speed'))[1]; return sp.max_mb === 20 && sp.max_s === 12; })()`, 6000, "обе правки записаны");
  },
  async "notify-409"() {
    // строка 25 для «Уведомлений»: шлются только изменённые виды; чужая правка того же вида — свежие данные и
    // «Применить снова»; неизвестный серверу вид не уходит (иначе 400 на всё «Сохранить»)
    await open("", "settings?tab=notify");          // UI-5: «Уведомления» — своя часть «Настроек»
    const card = `document.querySelector('[data-find="settings.notify"]')`;
    const sw = (txt) => `[...${card}.querySelectorAll('button[role=switch]')].find((b) => (b.closest('li') || b).textContent.startsWith(${JSON.stringify(txt)}))`;
    await step(`!!${card} && !!${sw("Выход упал")} && !!${sw("Склеивать сообщения о выходе")}`, 8000, "карточка с видами сервера");
    if (await js(`[...${card}.querySelectorAll('button[role=switch]')].some((b) => (b.closest('li') || b).textContent.includes('Связь с сервером вернулась'))`)) throw new Error("вид node_online, которого нет у сервера");
    await js(`${sw("Квота выхода")}.click(); true`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 3000);
    await js(`(async () => { const n = (await W.mock('GET', '/api/notify'))[1]; return W.mock('PUT', '/api/notify', { push: { exit_quota: !n.push.exit_quota }, ver: n.ver }); })()`);
    await mustClick("Сохранить", card);
    await step(`W.text(${card}).includes('Уведомления изменили, пока вы правили')`, 6000, "список «изменили»");
    await step(`(async () => (await W.mock('GET', '/api/notify'))[1].push.exit_quota === false)()`, 3000, "чужая запись на месте");
    // своя правка совпала с чужой — снята; правим другой вид и сохраняем: уходит только он
    await js(`${sw("Выход упал")}.click(); true`);
    await mustClick("Сохранить", card);
    await step(`(async () => { const n = (await W.mock('GET', '/api/notify'))[1]; return n.push.exit_dead === false && n.push.exit_quota === false; })()`, 6000, "записан только свой вид");
  },
  async "group-del-need"() {
    // «Удалить группу», на которую ссылаются устройство и прокси бота (REV-s1-t8-a С-2): окно спрашивает и это (по 409
    // «need» прогона), удаление проходит, маршрут устройства — на выбранную группу
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === 'd-7ae67ae0'); return W.mock('PUT', '/api/gw/devices/d-7ae67ae0/route', { via: 'group:g-5f1a2b', fail: 'block', ver: d.route_ver }); })()`);
    await step(`W.openCard('Стриминг') && !!document.querySelector('[data-eg-del="g-5f1a2b"]') && !document.querySelector('[data-eg-del="g-5f1a2b"]').disabled`, 6000, "кнопка «Удалить группу»");
    await js(`document.querySelector('[data-eg-del="g-5f1a2b"]').click(); true`);   // «⋯» в шапке карточки (№29)
    await step(`!!W.btn('Удалить группу…')`, 4000, "пункт «Удалить группу…» в меню");
    await js(`W.btn('Удалить группу…').click(); true`);
    const dlg = "[...document.querySelectorAll('.gx-dlg, [role=dialog]:not(.gx-sheet)')].pop()";
    await step(`!!${dlg} && W.text(${dlg}).includes('Куда направить пользователей и устройства')`, 6000, "выбор для пользователей и устройств");
    // «напрямую» людям не предлагается — это трафик мимо выходов; только зарубежные группы (REV-s1-t8-2 П-3, X10)
    const opts = await js(`[...${dlg}.querySelectorAll('select')].pop().querySelectorAll('option').length && [...[...${dlg}.querySelectorAll('select')].pop().options].map((o) => o.value)`);
    if (!opts || !opts.length || opts.some((v) => !/^(main|g-[0-9a-f]{6})$/.test(v))) throw new Error("в выборе для пользователей: " + JSON.stringify(opts));
    await mustClick("Удалить группу", dlg);
    await step(`!!W.btn('Удалить группу', ${dlg}) && !W.btn('Удалить группу', ${dlg}).disabled && !W.text(${dlg}).includes('Не удалось')`, 8000, "прогон без отказа");
    await mustClick("Удалить группу", dlg);
    await step(`(async () => { const [, p] = await W.mock('GET', '/api/gw/policy'); const [, pp] = await W.mock('GET', '/api/gw/people');
      return !p.policy.groups.some((g) => g.id === 'g-5f1a2b') && pp.devices.find((d) => d.id === 'd-7ae67ae0').route.via === 'pool'; })()`, 8000, "группа удалена, маршрут — «Основная»");
  },
  async "group-del-unsaved"() {
    // слияние t8b+t8c: «Удалить группу» переносит её выходы в «Куда перенести»; у группы-цели несохранённые правки —
    // «Удалить группу» в окне неактивна с причиной (как «Перенести» в окне переноса)
    await open("", "exits?tab=groups"); await step(groupsReady);
    await js(`W.setSelect(W.card('Основная').querySelector('.kk-eg-row select'), 'direct'); true`);
    await step(`W.openCard('Стриминг') && !!document.querySelector('[data-eg-del="g-5f1a2b"]') && !document.querySelector('[data-eg-del="g-5f1a2b"]').disabled`, 6000, "кнопка «Удалить группу» у «Стриминга»");
    await js(`document.querySelector('[data-eg-del="g-5f1a2b"]').click(); true`);   // «⋯» в шапке карточки (№29)
    await step(`!!W.btn('Удалить группу…')`, 4000, "пункт «Удалить группу…» в меню");
    await js(`W.btn('Удалить группу…').click(); true`);
    const dlg = "[...document.querySelectorAll('.gx-dlg, [role=dialog]:not(.gx-sheet)')].pop()";
    await step(`!!${dlg} && W.text(${dlg}).includes('Куда перенести')`, 6000, "выбор «Куда перенести»");
    await step(`!!W.btn('Удалить группу', ${dlg}) && W.btn('Удалить группу', ${dlg}).disabled && W.btn('Удалить группу', ${dlg}).title === 'Сначала сохраните или отмените правки'`, 4000, "«Удалить группу» неактивна: у группы-цели правки");
    const n = await js(`(async () => (await W.mock('GET', '/api/gw/policy'))[1].policy.groups.length)()`);
    if (n < 2) throw new Error("группа удалена при правках группы-цели");
  },
  async "rename-dup"() {
    // имя выхода, как у другого (REV-s1-t7 З-4): 409 confirm_duplicate — вопрос на месте и «Оставить» с allow_duplicate
    await open("", "exits"); await step(`!!document.querySelector('[data-hold-row="exit:e-7c2d8e0f13"]')`);
    await js(`document.querySelector('[data-hold-row="exit:e-7c2d8e0f13"] .gx-xt__x').click(); true`);
    await js(`document.querySelector('button[aria-label="Переименовать «Хельсинки»"]').click(); true`);
    await step(`!!document.querySelector('[role=dialog] input')`);
    await js(`(() => { const i = document.querySelector('[role=dialog] input'); i.value = 'Вена'; i.dispatchEvent(new Event('input')); return true; })()`);
    await mustClick("Сохранить имя", "document.querySelector('[role=dialog]')");
    await step(`W.text(document.querySelector('[role=dialog]')).includes('Имя «Вена» уже есть')`, 4000, "вопрос о дубле");
    await mustClick("Оставить", "document.querySelector('[role=dialog]')");
    await step(`(async () => (await W.mock('GET', '/api/gw/exits'))[1].exits.find((e) => e.id === 'e-7c2d8e0f13').name === 'Вена')()`, 6000, "имя сохранено");
  },
  async "rename-route"() {
    // T13: имя — вне версии маршрута. Лист устройства с выбранным маршрутом; другое место (вкладка, SSH) переименовало
    // устройство — «Применить» проходит без 409, в листе новое имя, маршрут записан
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`;
    await step(`!!${sel}`, 6000, "форма маршрута");
    await js(`W.setSelect(${sel}, 'group:g-5f1a2b'); true`);
    await js(`W.mock('PUT', '/api/gw/devices/d-7ae67ae0/name', { name: 'Телефон Оли' })`);
    await step(`W.text(document.querySelector('.gx-sheet')).includes('Телефон Оли')`, 6000, "новое имя в листе");
    await mustClick("Применить", "document.querySelector('.gx-sheet')");
    await mustClick("Применить", "document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)')");
    await step(`(async () => ((await W.mock('GET', '/api/gw/people'))[1].devices.find((d) => d.id === 'd-7ae67ae0').route || {}).via === 'group:g-5f1a2b')()`, 6000, "маршрут записан");
    await step(`!W.text().includes('изменили, пока вы правили')`, 1000, "без 409");
  },
  async "rename-device-dup"() {
    // T13: устройство с именем другого устройства того же человека — «Оставить такое же?», повтор с allow_duplicate;
    // подсказка сервера (note) — всплывающим сообщением; id и логин не меняются
    await open("", "people");
    const did = await js(`(async () => { const p = (await W.mock('GET', '/api/gw/people'))[1]; const u = p.users.find((x) => p.devices.filter((d) => d.user === x.id).length >= 2); const ds = p.devices.filter((d) => d.user === u.id); window.__rn = { id: ds[0].id, login: ds[0].login, other: ds[1].name, name: ds[0].name }; return ds[0].id; })()`);
    // UI-4: строка устройства в таблице К1 — раскрыть человека, «⋯» устройства → «Переименовать»
    await step(`!!document.querySelector('.gx-xt__r[data-did="${did}"]')`, 6000, "строка устройства");
    await js(`(() => { const g = document.querySelector('.gx-xt__r[data-did="${did}"]').closest('.gx-xt__g'); if (!g.classList.contains('is-open')) g.querySelector('.gx-xt__x').click(); return true; })()`);
    await js(`document.querySelector('.gx-xt__r[data-did="${did}"] .gx-menu-btn').click(); true`);
    await step(`!!document.querySelector('#layers .gx-menu')`, 3000, "меню устройства");
    await js(`[...document.querySelectorAll('#layers .gx-menu__item')].find((b) => b.textContent.trim() === 'Переименовать').click(); true`);
    await step(`!!document.querySelector('[role=dialog] .kk-rn__in')`, 4000, "окно имени");
    await step(`W.text(document.querySelector('[role=dialog]')).includes('из 40')`, 2000, "счётчик знаков");
    await js(`(() => { const i = document.querySelector('[role=dialog] .kk-rn__in'); i.value = window.__rn.other.toUpperCase(); i.dispatchEvent(new Event('input')); return true; })()`);
    await mustClick("Сохранить имя", "document.querySelector('[role=dialog]')");
    await step(`W.text(document.querySelector('[role=dialog]')).includes('уже есть')`, 4000, "вопрос о дубле");
    await mustClick("Оставить", "document.querySelector('[role=dialog]')");
    await step(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === window.__rn.id); return d.name === window.__rn.other.toUpperCase() && d.login === window.__rn.login; })()`, 6000, "имя сохранено, логин прежний");
    await step(`!document.querySelector('[role=dialog] .kk-rn__in')`, 4000, "окно закрылось");
    await step(`W.text().includes('Имя сохранено')`, 4000, "подсказка");
  },
  // ── повторный вход: полоса формы-одиночки зовёт перерисовку текущей карточки (REV-s1-t8-2 С-1, З-1) ──
  async "rv-speed-revisit-save"() {
    await open("", "settings?tab=health");          // круг 1 Т1: тест скорости — «Настройки › Проверка сервера»
    const card = `document.querySelector('[data-find="settings.speedtest"]')`;
    const expand = `[...document.querySelectorAll('[data-find="settings.speedtest"] button')].find((b) => b.getAttribute('aria-expanded') === 'false')`;
    await step(`!!${card} && !!${card}.querySelector('input')`, 8000, "карточка");
    await js(`location.hash = '#overview'; true`); await sleep(800);
    await js(`location.hash = '#settings?tab=health'; true`);
    await step(`!!${card}`, 8000, "часть снова"); await sleep(500);
    if (await js(`!!${expand}`)) await js(`${expand}.click(); true`);
    await step(`!!${card}.querySelector('input')`, 6000, "карточка снова");
    await js(`(() => { const i = ${card}.querySelectorAll('input.gx-input')[2]; i.value = '50'; i.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 4000, "изменений 1");
    await mustClick("Сохранить", card);
    await step(`(async () => (await W.mock('GET', '/api/gw/probes/speed'))[1].budget_day_mb === 50)()`, 6000, "записано");
    await sleep(800);
    await js(`(() => { const i = ${card}.querySelectorAll('input.gx-input')[0]; i.value = '20'; i.dispatchEvent(new Event('input')); return true; })()`);
    await sleep(500);
    const btn = await js(`!!W.btn('Сохранить', ${card})`);
    if (!btn) throw new Error("после сохранения на втором входе полосы «Сохранить» в карточке нет");
  },

  async "rv-notify-revisit-save"() {
    // второй вход: «Сохранить» проходит; после него полоса на месте и следующая правка сохраняется
    await open("", "settings?tab=notify");          // UI-5: «Уведомления» — своя часть «Настроек»
    const card = `document.querySelector('[data-find="settings.notify"]')`;
    const sw = (txt) => `[...${card}.querySelectorAll('button[role=switch]')].find((b) => (b.closest('li') || b).textContent.startsWith(${JSON.stringify(txt)}))`;
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка");
    await js(`location.hash = '#overview'; true`); await sleep(800);
    await js(`location.hash = '#settings?tab=notify'; true`);
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка снова");
    await sleep(500);
    await js(`${sw("Квота выхода")}.click(); true`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 3000, "изменений 1");
    await mustClick("Сохранить", card);
    await step(`(async () => (await W.mock('GET', '/api/notify'))[1].push.exit_quota === false)()`, 6000, "первая правка записана");
    await sleep(800);
    await js(`${sw("Выход упал")}.click(); true`);
    await sleep(500);
    const bar = await js(`W.text(${card}).includes('Изменений: 1')`);
    const btn = await js(`!!W.btn('Сохранить', ${card})`);
    if (!bar || !btn) throw new Error("после первого сохранения полосы нет: «Изменений: 1» = " + bar + ", «Сохранить» = " + btn);
  },

  async "rv-speed-revisit"() {
    // второй вход на вкладку «Группы»: 409 с чужой правкой того же поля — на экране свежее значение
    await open("", "settings?tab=health");          // круг 1 Т1: тест скорости — «Настройки › Проверка сервера»
    const card = `document.querySelector('[data-find="settings.speedtest"]')`;
    const expand = `[...document.querySelectorAll('[data-find="settings.speedtest"] button')].find((b) => b.getAttribute('aria-expanded') === 'false')`;
    await step(`!!${card} && !!${card}.querySelector('input')`, 8000, "карточка теста скорости");
    await js(`location.hash = '#overview'; true`); await sleep(800);
    await js(`location.hash = '#settings?tab=health'; true`);
    await step(`!!${card}`, 8000, "часть снова");
    await sleep(500);
    if (await js(`!!${expand}`)) await js(`${expand}.click(); true`);
    await step(`!!${card}.querySelector('input')`, 6000, "карточка теста скорости снова");
    await js(`(() => { const i = ${card}.querySelectorAll('input.gx-input')[2]; i.value = '50'; i.dispatchEvent(new Event('input')); return true; })()`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 4000, "изменений 1");
    await js(`(async () => { const sp = (await W.mock('GET', '/api/gw/probes/speed'))[1]; return W.mock('PUT', '/api/gw/probes/speed', { budget_day_mb: 30, ver: sp.ver }); })()`);
    await mustClick("Сохранить", card);
    await sleep(2500);
    const shown = await js(`${card}.querySelectorAll('input.gx-input')[2].value`);
    if (shown !== "30") throw new Error("на экране " + shown + ", на сервере и в черновике 30; кнопка «Применить снова»: " + (await js(`!!W.btn('Применить снова', ${card})`)));
  },

  async "rv-notify-revisit"() {
    // второй вход в «Настройки»: «Отменить» должна вернуть переключатель на экране
    await open("", "settings?tab=notify");          // UI-5: «Уведомления» — своя часть «Настроек»
    const card = `document.querySelector('[data-find="settings.notify"]')`;
    const sw = (txt) => `[...${card}.querySelectorAll('button[role=switch]')].find((b) => (b.closest('li') || b).textContent.startsWith(${JSON.stringify(txt)}))`;
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка");
    await js(`location.hash = '#overview'; true`); await sleep(800);
    await js(`location.hash = '#settings?tab=notify'; true`);
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка снова");
    await sleep(500);
    const v0 = await js(`${sw("Квота выхода")}.getAttribute('aria-checked')`);
    await js(`${sw("Квота выхода")}.click(); true`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 3000, "изменений 1");
    await mustClick("Отменить", card);
    await sleep(600);
    const v1 = await js(`${sw("Квота выхода")}.getAttribute('aria-checked')`);
    const bar = await js(`W.text(${card}).includes('Изменений: 1')`);
    if (v1 !== v0) throw new Error("после «Отменить» переключатель на экране " + v1 + ", в черновике " + v0 + "; полоса «Изменений: 1» = " + bar);
  },
  async "rv-notify-revisit-409"() {
    // второй вход; чужая правка другого вида — молча; экран должен показать свежее значение
    await open("", "settings?tab=notify");          // UI-5: «Уведомления» — своя часть «Настроек»
    const card = `document.querySelector('[data-find="settings.notify"]')`;
    const sw = (txt) => `[...${card}.querySelectorAll('button[role=switch]')].find((b) => (b.closest('li') || b).textContent.startsWith(${JSON.stringify(txt)}))`;
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка");
    await js(`location.hash = '#overview'; true`); await sleep(800);
    await js(`location.hash = '#settings?tab=notify'; true`);
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка снова");
    await sleep(500);
    await js(`${sw("Квота выхода")}.click(); true`);
    await js(`(async () => { const n = (await W.mock('GET', '/api/notify'))[1]; return W.mock('PUT', '/api/notify', { push: { exit_dead: !n.push.exit_dead }, ver: n.ver }); })()`);
    await mustClick("Сохранить", card);
    await sleep(1500);
    const srv = await js(`(async () => String((await W.mock('GET', '/api/notify'))[1].push.exit_dead))()`);
    const shown = await js(`${sw("Выход упал")}.getAttribute('aria-checked')`);
    if (shown !== srv) throw new Error("на экране " + shown + ", на сервере " + srv);
  },
  async "notify-rebuild"() {
    // раздел «Настройки» пересобирается на месте (смена «Подробности») — полоса «Уведомлений» рисует новую карточку
    await open("", "settings?tab=notify");          // UI-5: «Уведомления» — своя часть «Настроек»
    const card = `document.querySelector('[data-find="settings.notify"]')`;
    const sw = (txt) => `[...${card}.querySelectorAll('button[role=switch]')].find((b) => (b.closest('li') || b).textContent.startsWith(${JSON.stringify(txt)}))`;
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка");
    // UI-6: окна «Вид» на компьютере больше нет («Вид» в шапке ведёт в «Настройки › Вид») — раздел пересобирается на
    // месте сменой раскладки (окно уже 1100 и обратно: resize → rebuildPage), как при смене «Подробности»
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1050, height: 1000, deviceScaleFactor: 1, mobile: false }); await sleep(500);
    await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false }); await sleep(500);
    await step(`!!${card} && !!${sw("Квота выхода")}`, 8000, "карточка после пересборки"); await sleep(400);
    const v0 = await js(`${sw("Квота выхода")}.getAttribute('aria-checked')`);
    await js(`${sw("Квота выхода")}.click(); true`);
    await step(`W.text(${card}).includes('Изменений: 1')`, 3000, "изменений 1");
    await mustClick("Отменить", card);
    await sleep(600);
    const v1 = await js(`${sw("Квота выхода")}.getAttribute('aria-checked')`);
    if (v1 !== v0 || (await js(`W.text(${card}).includes('Изменений: 1')`))) throw new Error("после «Отменить» на экране " + v1 + ", в черновике " + v0);
  },
  async "rv-route-inherit-409"() {
    // свой маршрут устройства → «как у человека»; бот меняет маршрут; 409 — «Ваш выбор: …» словами
    await open("", "people?focus=d-7ae67ae0&open=settings");
    const sel = `document.querySelector('.gx-sheet .kk-route select')`, sheet = "document.querySelector('.gx-sheet')";
    const dlg = "document.querySelector('.gx-dlg, [role=dialog]:not(.gx-sheet)')";
    await step(`!!${sel}`, 6000, "форма маршрута");
    await js(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === 'd-7ae67ae0'); return W.mock('PUT', '/api/gw/devices/d-7ae67ae0/route', { via: 'group:g-5f1a2b', fail: 'block', ver: d.route_ver }); })()`);
    await step(`${sel}.value === 'group:g-5f1a2b'`, 6000, "свой маршрут на экране");
    await js(`W.setSelect(${sel}, 'inherit'); true`);
    await js(`(async () => { const d = (await W.mock('GET', '/api/gw/people'))[1].devices.find((x) => x.id === 'd-7ae67ae0'); return W.mock('PUT', '/api/gw/devices/d-7ae67ae0/route', { via: 'block', fail: 'block', ver: d.route_ver }); })()`);
    await sleep(1500);
    await mustClick("Применить", sheet);
    await mustClick("Применить", dlg);
    await step(`W.text(${sheet}).includes('Ваш выбор: ')`, 6000, "«Ваш выбор»");
    const line = await js(`W.text(${sheet}).split('\\n').find((l) => l.includes('Ваш выбор: '))`);
    if (/inherit/.test(line)) throw new Error("строка: " + line);
  },
};

await cdp("Page.enable"); await cdp("Runtime.enable");
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
const only = (process.argv[2] || "").split(",").filter(Boolean);
let bad = 0, runs = 0;
for (const [name, fn] of Object.entries(SCENARIOS)) {
  if (only.length && !only.includes(name)) continue;
  runs++;
  jsErrs.splice(0);
  try { await fn(); } catch (e) { bad++; console.log(`FAIL ${name}: ${e.message}`); continue; }
  const errs = jsErrs.splice(0);
  if (errs.length) { bad++; console.log(`FAIL ${name}: js | ${errs[0].slice(0, 300)}`); continue; }
  console.log(`ok   ${name}`);
}
console.log(bad ? `WRITES FAIL: ${bad} из ${runs}` : `WRITES OK: ${runs} сценариев`);
process.exit(bad ? 1 : 0);
