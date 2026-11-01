// k3_rug_check.mjs — T15 Q6: «Российские группы» в настоящем Chrome на заглушке панели (?mock=1&rug=…).
// Что проверяется (tests/test_q6_ru_screens.py читает JSON из stdout):
//   last    — «Выходы › Российские группы», «Дом» на последнем шаге: пункт подменю после «Группы» с числом 2 и жёлтым
//             тоном, «Прокси API Telegram» в подменю нет; карточки «Дом» и «Офис» — общая карточка групп (kk-eg-card--ru,
//             РФ-группы Ш1); метка «сейчас: …» и неработающие ключи; ключи по порядку; раскрытие «Дома» (стрелка, факты);
//   ok      — «Дом» через «Роутер»: метка зелёная, подменю без жёлтого тона;
//   rudown  — сервер не достаёт до российских адресов: «ждём связи»;
//   none    — узел без российских групп (как 3.3.7.9 после обновления): «Российских групп пока нет», число у пункта нет;
//             «Все выходы» — без строки-ссылки; в «Правилах» — «Напрямую с сервера — у всех»;
//   emptyg  — «Дом» без ключей: «Добавить ключ в «Дом»»;
//   all     — «Все выходы»: российских выходов в таблице нет, внизу строка «Российские выходы: 3»;
//   key     — окно ключа с назначением «+ru» на узле без групп: российский выбран, «Новая «Дом»»; добавление создаёт
//             российскую группу «Дом» и кладёт ключ в неё (роль ru);
//   geo     — 768 и 1440: ширина значения фактов, место «сейчас: …», пустые ячейки шапки списка выходов;
//   free    — группы есть, никому не выбраны: «Российские сайты» — напрямую у всех, кому не выбрана группа;
//   country — «Дача» сменила страну: российский выход не снимается, кнопка условия «Страна не та»;
//   keyg    — окно ключа с группой «Офис»: российский выбран, выбор — «Офис», подзаголовок — про роутер в России; зарубежный по умолчанию группу не шлёт;
//   move    — «Перенести в группу…» «Роутера» в «Стриминг»: предупреждение о смене роли, перенос проходит (confirm_role),
//             выход становится зарубежным;
//   rules   — «Правила › Российские сайты»: группы выбраны — «Через российскую группу:» и «Выбрать пользователей…»;
//   pick    — окно выбора: снять «Маму», отметить «Гости» — «новое», «Изменится: …», одно сохранение — в заглушке
//             у «Мамы» выбора нет, у «Гостей» — «Дом»;
//   en      — EN: подпись части и строки правил по-английски.
// node tools/k3_rug_check.mjs <адрес статики панели> <chrome>
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3rug-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_rug_check: время вышло"); process.exit(3); }, 240000).unref();
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
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
async function open(qs, hash, ready) {
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1${qs || ""}#${hash}` });
  await until(`!!window.KASKAD_MOCK && !!window.KaskadPanel`, 20000);
  await until(ready, 20000);
  await sleep(500);
}
const RU_PART = `!!document.querySelector('.kk-eg-card--ru, .kk-empty')`;
// подменю «Выходов»: пункт части, его число и тон; соседи
const SUBNAV = `(() => { const n = document.querySelector('nav[data-subnav="exits"]'); if (!n) return null;
  const ids = [...n.querySelectorAll('.gx-subnav__i[data-part]')].map((a) => a.dataset.part);
  const ru = n.querySelector('.gx-subnav__i[data-part="ru"]'), c = ru && ru.querySelector('.gx-subnav__n');
  return { ids, n: c ? c.textContent : null, warn: !!(c && c.classList.contains('gx-subnav__n--warn')), tg: /Telegram/.test(n.textContent), cur: ru ? ru.getAttribute('aria-current') : null }; })()`;
// российская группа — общая карточка групп (РФ-группы Ш1): имя — кнопка в шапке, «сейчас: …» — значок справа; ключи и
// подсказки — в раскрытой карточке (down — ключи с красным «не работает»)
const CARDS = `[...document.querySelectorAll('.kk-eg-card--ru')].map((c) => ({ name: c.querySelector('.kk-eg-head__n').textContent,
  now: (c.querySelector('.kk-eg-head__s > .gx-chip') || {}).textContent || "", tone: ((c.querySelector('.kk-eg-head__s > .gx-chip') || {}).className || "").replace(/.*gx-chip--(\\w+).*/, "$1"),
  down: [...c.querySelectorAll('.kk-eg-exit')].filter((r) => r.querySelector('.kk-eg-st .gx-chip--danger')).map((r) => r.querySelector('.kk-eg-exit__n > b').textContent),
  empty: [...c.querySelectorAll('.kk-eg-blk > .gx-hint')].map((x) => x.textContent).join(" ") }))`;
// раскрыть все российские карточки (по одной: карточка перерисовывается)
const EXPAND = `for (const b of document.querySelectorAll('.kk-eg-card--ru .kk-eg-tog[aria-expanded="false"]')) { b.click(); await new Promise((r) => setTimeout(r, 300)); }`;
const out = {};

// last: «Дом» на последнем шаге
await open("&rug=last", "exits?tab=ru", RU_PART);
out.last = await js(`(async () => {
  const sub = ${SUBNAV};
  const tog = document.querySelector('.kk-eg-card--ru .kk-eg-tog'), exp0 = tog.getAttribute('aria-expanded');
  tog.click(); await new Promise((r) => setTimeout(r, 300));
  const t2 = document.querySelector('.kk-eg-card--ru .kk-eg-tog'), exp1 = t2.getAttribute('aria-expanded'), focus = document.activeElement === t2;
  const facts = [...document.querySelectorAll('.kk-rukv__k')].map((x) => x.textContent);
  // ключи — уровнями в общей карточке своей группы (РФ-группы Ш1): раскрыть остальные и прочитать строки по порядку
  ${EXPAND}
  const rows = [...document.querySelectorAll('.kk-eg-card--ru .kk-eg-exit__n > b')].map((x) => x.textContent);
  const cards = ${CARDS};   // неработающие ключи — в раскрытой карточке
  return { sub, cards, rows, exp0, exp1, focus, facts, title: document.querySelector('.kk-part').getAttribute('aria-label') };
})()`);
// ok и rudown
await open("&rug=ok", "exits?tab=ru", RU_PART);
out.ok = await js(`({ sub: ${SUBNAV}, cards: ${CARDS} })`);
await open("&rug=rudown", "exits?tab=ru", RU_PART);
out.rudown = await js(`({ cards: ${CARDS} })`);
// none: узел без российских групп
await open("", "exits?tab=ru", RU_PART);
out.none = await js(`({ sub: ${SUBNAV}, empty: !!document.querySelector('[data-find="exits.rugEmpty"]'), text: (document.querySelector('.kk-empty__t') || {}).textContent || "",
  btns: [...document.querySelectorAll('.kk-empty__b button')].map((b) => b.textContent) })`);
await open("", "exits?tab=list", `!!document.querySelector('.gx-xt')`);
out.none.link = await js(`!!document.querySelector('.kk-rux__link')`);
await open("", "rules?tab=profile", `!!document.querySelector('.kk-rs')`);
out.none.rules = await js(`({ main: document.querySelector('.kk-rs__main').textContent, btn: document.querySelector('.kk-rs__acts button').textContent })`);
// emptyg: «Дом» без ключей
await open("&rug=empty", "exits?tab=ru", RU_PART);
out.emptyg = await js(`(async () => { ${EXPAND}
  return { cards: ${CARDS}, btn: ([...document.querySelectorAll('.kk-eg-card--ru button')].find((b) => /Добавить ключ/.test(b.textContent)) || {}).textContent || "" }; })()`);
// all: «Все выходы» без российских
await open("&rug=ok", "exits?tab=list", `!!document.querySelector('.gx-xt__r, .gx-xt__empty')`);
out.all = await js(`({ names: [...document.querySelectorAll('.gx-xt .kk-exit')].map((g) => g.textContent),
  link: (document.querySelector('.kk-rux__link') || {}).textContent || "", href: (document.querySelector('.kk-rux__link a') || {}).getAttribute ? document.querySelector('.kk-rux__link a').getAttribute('href') : "" })`);
// key: «Добавить российский ключ» на узле без групп — новая «Дом» и ключ в ней
await open("", "exits?tab=ru", RU_PART);
out.key = await js(`(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  const b = [...document.querySelectorAll('.kk-empty__b button')].find((x) => /внутренний ключ/.test(x.textContent)); b.click(); await s(500);
  const radios = [...document.querySelectorAll('.kk-dest [role="radio"]')].map((r) => [r.dataset.role, r.getAttribute('aria-checked')]);
  const picks = [...document.querySelectorAll('.kk-dest .gx-pick')].map((p) => p.textContent);
  const ta = document.querySelector('.gx-dlg textarea'); ta.value = "vless://00000000-0000-4000-8000-000000000001@dacha.example.com:443?security=reality&sni=example.com#Dacha"; ta.dispatchEvent(new Event('input', { bubbles: true }));
  const btn = (re) => [...document.querySelectorAll('.gx-dlg button')].find((x) => re.test(x.textContent));
  btn(/Проверить/).click(); await s(700);
  const add = btn(/^\\s*Добавить/); if (!add) return { radios, picks, err: "no add" };
  add.click(); await s(1200);
  const db = KASKAD_MOCK.db(), g = (db.policy ? db.policy.groups : []).filter((x) => x.role === "ru");
  const ex = db.exits.filter((e) => e.role === "ru");
  return { radios, picks, open: !!document.querySelector('.gx-dlg textarea'), groups: g.map((x) => x.name), ruExits: ex.length,
    placed: g.length ? [].concat(...g[0].tiers.map((t) => t.exits)).length : 0 };
})()`);
// keyg: окно с группой «Офис»; зарубежный по умолчанию группу не шлёт
await open("&rug=ok", "exits?tab=ru", RU_PART);
out.keyg = await js(`(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  KaskadPanel.addKey({ group: "g-0ff1ce" }); await s(500);
  const radios = [...document.querySelectorAll('.kk-dest [role="radio"]')].map((r) => [r.dataset.role, r.getAttribute('aria-checked')]);
  const picks = [...document.querySelectorAll('.kk-dest .gx-pick')].map((p) => p.textContent);
  const subT = () => (document.querySelector('.gx-dlg .gx-sheet__sub') || {}).textContent;
  const sub0 = subT();
  document.querySelector('.kk-dest [data-role="abroad"]').click(); await s(200);
  const after = [...document.querySelectorAll('.kk-dest [role="radio"]')].map((r) => [r.dataset.role, r.getAttribute('aria-checked')]);
  return { radios, picks, after, sub0, sub1: subT() };
})()`);
// free: группы есть, никому не выбраны — строка «Российских сайтов» в состоянии умолчания (кадр 4.2, второе)
await open("&rug=free", "rules?tab=profile", `!!document.querySelector('.kk-rs')`);
out.free = await js(`({ main: document.querySelector('.kk-rs__main').textContent, sub: document.querySelector('.kk-rs__sub').textContent,
  btn: document.querySelector('.kk-rs__acts button').textContent })`);
// country: «Дача» сменила страну (rug=last) — деталь говорит, что российский выход из-за страны не снимается, и
// предлагает условие «Страна не та» (кадр 3.3)
await open("&rug=last", "exits?tab=ru", RU_PART);
out.country = await js(`(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  ${EXPAND}
  const b = [...document.querySelectorAll('.kk-eg-card--ru .kk-eg-exit__n')].find((x) => /Дача/.test(x.textContent)); if (!b) return { err: "no row" };
  b.click(); await s(700);
  const al = document.querySelector('.kk-eg-country');
  return al ? { text: al.querySelector('span').textContent, btns: [...al.querySelectorAll('button')].map((x) => x.textContent.trim()) } : { err: "no alert" };
})()`);
// move: «Роутер» → «Стриминг» (смена роли)
await open("&rug=ok", "exits?tab=ru", RU_PART);
out.move = await js(`(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  ${EXPAND}
  document.querySelector('.kk-eg-card--ru .kk-eg-exit [aria-haspopup="menu"]').click(); await s(400);
  const it = [...document.querySelectorAll('.gx-menu button, .gx-menu [role="menuitem"]')].find((b) => /Перенести/.test(b.textContent)); it.click(); await s(600);
  const heads = [...document.querySelectorAll('.gx-dlg .kk-eg-l')].map((x) => x.textContent);
  const here = [...document.querySelectorAll('.gx-dlg .gx-radio__i')].find((b) => /Дом/.test(b.textContent));
  [...document.querySelectorAll('.gx-dlg .gx-radio__i')].find((b) => /Стриминг/.test(b.textContent)).click(); await s(300);
  const warn = (document.querySelector('.gx-dlg .gx-al--warning') || {}).textContent || "";
  document.querySelector('.gx-dlg .kk-ca__btn').click(); await s(900);
  const err = [...document.querySelectorAll('.gx-dlg .kk-ca__t--danger')].map((x) => x.textContent).join(" ");
  const yes = document.querySelector('.gx-dlg .kk-ca__acts .ga-btn--primary'); if (yes) { yes.click(); await s(1200); }
  const db = KASKAD_MOCK.db(), e = db.exits.find((x) => x.id === "e-a0a0a0a001"), g = db.policy.groups.find((x) => x.id === "g-5f1a2b");
  return { heads, hereDis: here ? here.getAttribute('aria-disabled') : null, warn, err, yes: !!yes, role: e.role || "abroad", inStream: [].concat(...g.tiers.map((t) => t.exits)).indexOf("e-a0a0a0a001") >= 0 };
})()`);
// rules + pick
await open("&rug=ok", "rules?tab=profile", `!!document.querySelector('.kk-rs')`);
out.rules = await js(`({ main: document.querySelector('.kk-rs__main').textContent, chips: [...document.querySelectorAll('.kk-rs__main .gx-chip')].map((c) => c.textContent),
  btn: document.querySelector('.kk-rs__acts button').textContent })`);
out.pick = await js(`(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  document.querySelector('.kk-rs__acts button').click(); await s(700);
  const row = (name) => [...document.querySelectorAll('.kk-ulist .gx-check__i')].find((b) => b.querySelector('.kk-ulist__n').textContent === name);
  const tabs = [...document.querySelectorAll('.gx-dlg [role="tab"]')].map((b) => [b.textContent, b.getAttribute('aria-selected')]);
  const st0 = ["Мама", "Папа", "Гости"].map((n) => row(n) ? row(n).getAttribute('aria-checked') : null);
  const save0 = [...document.querySelectorAll('.gx-dlg button')].find((b) => b.textContent === "Сохранить").disabled;
  row("Мама").click(); await s(150); row("Гости").click(); await s(150);
  const chips = [...document.querySelectorAll('.kk-ulist .gx-chip')].map((c) => c.closest('.gx-check__i').querySelector('.kk-ulist__n').textContent);
  const sum = document.querySelector('.kk-psum').textContent;
  const sv = [...document.querySelectorAll('.gx-dlg button')].find((b) => b.textContent === "Сохранить"); const save1 = sv.disabled;
  sv.click(); await s(1200);
  const db = KASKAD_MOCK.db(), u = (n) => db.users.find((x) => x.name === n);
  return { tabs, st0, save0, chips, sum, save1, closed: !document.querySelector('.kk-ulist'), mama: u("Мама").ru_via || "inherit", guests: u("Гости").ru_via || "inherit", papa: u("Папа").ru_via || "inherit" };
})()`);
// REV-s1fin П-3: выбор сменили в другом окне между чтением и «Сохранить» — 409 object_changed, ничего не записано,
// окно заново с текущим выбором; строка без ver (вкладка до обновления) — 400 ver_required
await open("&rug=ok", "rules?tab=profile", `!!document.querySelector('.kk-rs')`);
out.pick409 = await js(`(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  document.querySelector('.kk-rs__acts button').click(); await s(700);
  const row = (name) => [...document.querySelectorAll('.kk-ulist .gx-check__i')].find((b) => b.querySelector('.kk-ulist__n').textContent === name);
  row("Гости").click(); await s(150);
  const db = KASKAD_MOCK.db(), u = (n) => db.users.find((x) => x.name === n);
  u("Гости").ru_via = "direct";
  [...document.querySelectorAll('.gx-dlg button')].find((b) => b.textContent === "Сохранить").click(); await s(1500);
  const toast = [...document.querySelectorAll('.gx-toast')].map((x) => x.textContent).join(" | ");
  const again = !!document.querySelector('.kk-ulist'), st1 = row("Гости") ? row("Гости").getAttribute('aria-checked') : null;
  const r = await KASKAD_MOCK.fetch("/api/rules/ru-via", { method: "PUT", headers: { "X-Kaskad-Admin": "1" }, body: JSON.stringify({ items: [{ user: u("Папа").id, via: "inherit" }] }) });
  return { toast, again, st1, guests: u("Гости").ru_via, papa: u("Папа").ru_via, old: [r.status, (await r.json()).data] };
})()`);
// en
await open("&rug=ok&lang=en", "exits?tab=ru", RU_PART);
out.en = { part: await js(`document.querySelector('.kk-part').getAttribute('aria-label')`), lead: await js(`document.querySelector('.kk-rus__why').textContent`) };
await open("&rug=ok&lang=en", "rules?tab=profile", `!!document.querySelector('.kk-rs')`);
out.en.rules = await js(`document.querySelector('.kk-rs__t').textContent + " | " + document.querySelector('.kk-rs__acts button').textContent`);
// geo: вёрстка части по ширинам — 768 (часть ~450 px рядом с подменю): факты подписью над значением, значение не
// столбиком; «сейчас: …» под именем; в шапке списка выходов нет пустых ячеек; 1440 — «сейчас» в строке имени (кадр 1.1)
const GEO = `(async () => {
  const s = (ms) => new Promise((r) => setTimeout(r, ms));
  ${EXPAND}
  const W = (q) => [...document.querySelectorAll(q)].map((n) => Math.round(n.getBoundingClientRect().width));
  // «сейчас» рядом с именем — по вертикали внутри блока «имя + сводка» и правее имени (в общей карточке значок по центру шапки)
  const now = document.querySelector('.kk-eg-card--ru .kk-eg-head__s > .gx-chip').getBoundingClientRect(), nb = document.querySelector('.kk-eg-card--ru .kk-eg-head__b').getBoundingClientRect();
  // в шапке уровня пустой — только последний столбец (меню уровня)
  return { kv: Math.min(...W('.kk-rukv__v')), nowBeside: now.top < nb.bottom - 4 && now.bottom > nb.top + 4 && now.left > nb.left + 100,
    empty: [...document.querySelectorAll('.kk-eg-card--ru .kk-eg-tier__h > *:not(:last-child)')].filter((n) => !n.textContent.trim()).length };
})()`;
out.geo = {};
for (const w of [768, 1440]) {
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: 900, deviceScaleFactor: 1, mobile: false });
  await open("&rug=last", "exits?tab=ru", RU_PART);
  out.geo[w] = await js(GEO);
}
console.log(JSON.stringify(out));
process.exit(0);
