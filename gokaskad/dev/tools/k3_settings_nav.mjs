// k3_settings_nav.mjs — UI-5 (шаблон T13 «Страница с подменю»): «Настройки» с подменю второго уровня в настоящем
// Chrome по заглушке панели (?mock=1). node tools/k3_settings_nav.mjs <адрес static> <путь к Chrome>
// Печатает одну строку JSON: то, что увидел, — проверки делает tests/test_ui5_settings.py.
//   addr   — адреса: синоним #settings/<часть>, без части, неизвестная часть, старые ?focus=/?open=
//   nav    — подменю: группы, aria, пункты; переход щелчком — новая запись истории, фокус на части, шапка «Настройки › Группа ›»
//   hist   — «Назад» / «Вперёд» между частями
//   keys   — ↑ / ↓ по кругу, Home / End
//   dirty  — правка «Уведомлений»: метка «правки», уход — окно; «Остаться» — адрес и часть те же; «Не сохранять» — переход
//   nt     — «Уведомления» (кадр Н8): полоса «В бот · Все», группы карточками колонками, строка 32, «все» у группы
//   time   — «Время»: смена пояса — метка «правки» и полоса; «Сохранить» — пояс на узле; чужая смена — «Применить снова»
//   att    — метки внимания: «истекает» (сертификат домена), «не настроен» (бот)
//   find   — каждая запись описи settings.*: часть из адреса записи, поле (data-find) в этой части и в фокусе
//   phone  — 390: список частей с подписями групп, строка → часть, «‹ Настройки» → список
//   en     — EN: подпись подменю и группы
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3stnav-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_settings_nav: время вышло"); process.exit(3); }, 240000).unref();
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
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { if (await js(expr)) return true; await sleep(120); } throw new Error("не дождался: " + expr); }
const view = (w, h) => cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
async function open(hash, lang = "ru", q = "") {
  const ready = "!!document.querySelector('.kk-part, .gx-mlist')";
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=${lang}${q}#${hash}` });
  await until(`document.readyState === 'complete' && ${ready}`, 20000);
  await sleep(700);
}
const go = async (hash, ms = 700) => { await js(`(() => { location.hash = ${JSON.stringify(hash)}; return true; })()`); await sleep(ms); };
const PART = `(document.querySelector('section[data-page="settings"] .kk-part') || {}).dataset`;
const STATE = `({ hash: location.hash, part: (${PART} || {}).part || null, cur: (document.querySelector('.gx-subnav__i[aria-current="page"]') || { dataset: {} }).dataset.part || null,
  h1: (document.querySelector('#head .gx-h1') || {}).textContent || null, crumb: [...document.querySelectorAll('#head .gx-crumb--path > span')].map((x) => x.textContent),
  active: document.activeElement === document.querySelector('.kk-part'), hl: history.length })`;
const out = {};
await view(1440, 1000);

// ── адреса ──
await open("settings/gateway");
out.addr = { syn: await js(STATE) };
await go("#settings/site?focus=settings.site", 1500); out.addr.synFocus = await js(STATE);
await go("#settings?tab=nope"); out.addr.unknown = await js(STATE);
await go("#settings"); out.addr.none = await js(STATE);
await go("#settings?open=host", 1500); out.addr.openHost = await js(STATE);
await go("#settings?focus=settings.mcp", 1500); out.addr.oldFocus = await js(STATE);
await go("#settings?focus=settings.reqPw", 1500); out.addr.oldItem = await js(STATE);

// ── подменю ──
await go("#settings?tab=look");
out.nav = await js(`(() => { const nav = document.querySelector('nav.gx-subnav');
  return { label: nav.getAttribute('aria-label'), groups: [...nav.querySelectorAll('.gx-subnav__h')].map((h) => ({ t: h.textContent, id: h.id, ul: h.nextElementSibling.getAttribute('aria-labelledby'),
    items: [...h.nextElementSibling.querySelectorAll('a.gx-subnav__i')].map((a) => ({ part: a.dataset.part, t: a.querySelector('.gx-subnav__l').textContent, href: a.getAttribute('href'), cur: a.getAttribute('aria-current') })) })),
    width: Math.round(nav.getBoundingClientRect().width), sideW: Math.round(document.querySelector('.gx-side').getBoundingClientRect().width), partW: Math.round(document.querySelector('.kk-part').getBoundingClientRect().width),
    secW: Math.round(document.querySelector('section[data-page="settings"]').getBoundingClientRect().width),
    over: [...nav.querySelectorAll('a.gx-subnav__i')].filter((a) => a.getBoundingClientRect().right > nav.getBoundingClientRect().right + 0.5 || a.querySelector('.gx-subnav__l').scrollWidth > a.querySelector('.gx-subnav__l').clientWidth).length }; })()`);
// ширина подменю по полосам (таблица ширин r4): 900–1439 — 164, 1440–1999 — 184, ≥ 2000 — 200
out.nav.bands = {};
for (const w of [1100, 1280, 1366, 1439, 1440, 1999, 2000]) {
  await view(w, 1000); await sleep(300);
  out.nav.bands[w] = await js(`Math.round(document.querySelector('nav.gx-subnav').getBoundingClientRect().width)`);
}
// слово подписи подменю — целиком на одной строке: перенос только между словами (24.10: «Груп-пы пользователей», «Здоро-вье»)
const SPLIT = `(() => { const out = []; document.querySelectorAll('.gx-subnav__l').forEach((l) => { if (!l.getClientRects().length) return;
  const tw = document.createTreeWalker(l, NodeFilter.SHOW_TEXT); for (let n; (n = tw.nextNode());) for (const m of n.data.matchAll(/[\\p{L}\\p{N}]+/gu)) {
    const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
    if (new Set([...r.getClientRects()].filter((x) => x.width > 0.5).map((x) => Math.round(x.top))).size > 1) out.push(l.textContent + ': ' + m[0]); } });
  return out; })()`;
out.nav.split = {};
for (const page of ["#settings?tab=look", "#overview", "#exits", "#people"]) for (const w of [768, 1024, 1100, 1280, 1440, 2000]) {
  await view(w, 1000); await go(page);
  const s = await js(SPLIT); if (s.length) out.nav.split[page + " " + w] = s;
}
await go("#settings?tab=look");
await view(1440, 1000); await sleep(300);
const hl0 = await js("history.length");
await js(`document.querySelector('.gx-subnav__i[data-part="access"]').click(), true`); await sleep(700);
out.nav.click = await js(STATE); out.nav.click.pushed = out.nav.click.hl - hl0;
await js(`document.querySelector('.gx-subnav__i[data-part="backup"]').click(), true`); await sleep(700);
out.nav.click2 = await js(STATE);

// ── «Назад» / «Вперёд» ──
await js("history.back(), true"); await sleep(800); out.hist = { back: await js(STATE) };
await js("history.back(), true"); await sleep(800); out.hist.back2 = await js(STATE);
await js("history.forward(), true"); await sleep(800); out.hist.fwd = await js(STATE);

// ── клавиши ──
out.keys = await js(`(() => { const all = [...document.querySelectorAll('.gx-subnav__i')]; all[0].focus(); const r = [];
  for (const k of ["ArrowUp", "ArrowDown", "ArrowDown", "End", "Home", "ArrowDown", "ArrowDown", "ArrowDown"]) { document.activeElement.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true })); r.push(document.activeElement.dataset.part); }
  return r; })()`);

// ── правки «Уведомлений» и уход ──
await go("#settings?tab=notify", 1500);
await js(`document.querySelector('.kk-part [role="switch"]').click(), true`); await sleep(300);
const ATT = `(() => { const a = document.querySelector('.gx-subnav__i[aria-current="page"] .gx-att'); return a ? a.className + "|" + a.textContent : null; })()`;
out.dirty = { att: await js(ATT) };
await js(`document.querySelector('.gx-subnav__i[data-part="access"]').click(), true`); await sleep(500);
const DLG = `document.querySelector('.gx-dlg-bd [role="alertdialog"], [role="alertdialog"]')`;
out.dirty.ask = await js(`!!${DLG}`);
await js(`[...${DLG}.querySelectorAll('button')].find((b) => b.textContent.trim() === "Остаться").click(), true`); await sleep(500);
out.dirty.stay = await js(STATE); out.dirty.stayAtt = await js(ATT);
await js(`document.querySelector('.gx-subnav__i[data-part="access"]').click(), true`); await sleep(500);
await js(`[...${DLG}.querySelectorAll('button')].find((b) => b.textContent.trim() === "Не сохранять").click(), true`); await sleep(800);
out.dirty.discard = await js(STATE);
// пункт главного меню «Настройки» из части с правками — часть и правки остаются
await go("#settings?tab=notify", 1500);
await js(`document.querySelector('.kk-part [role="switch"]').click(), true`); await sleep(300);
await js(`document.querySelector('#side [data-goto="settings"]').click(), true`); await sleep(600);
out.dirty.menu = await js(STATE); out.dirty.menuAtt = await js(ATT);
await js(`(() => { const b = [...document.querySelectorAll('.kk-part button')].find((x) => /Отменить/.test(x.textContent)); if (b) b.click(); return true; })()`); await sleep(400);
out.dirty.cancelAtt = await js(ATT);

// ── поиск: записи описи (argv[4]: [[id, at, need]]); записи, которым нужен домен, — на заглушке с доменом и сайтом (site=on) ──
const items = JSON.parse(process.argv[4] || "[]");
const DOMAIN_NEED = ["domain", "maskAvail", "maskOn", "panelAddr"];       // секретный адрес панели — у панели на домене с сайтом
// ── «Уведомления» по кадру Н8 (nav2 r3): часть в одном экране 1440×900, группы карточками колонками, «все» (вкл / выкл / смешано), ⓘ ──
await view(1440, 900);
await open("settings?tab=notify", "ru", "&bot=ok");
const NT = `(() => { const m = document.querySelector('.gx-main'); const g = [...document.querySelectorAll('.gx-ngrp')]; const h = document.querySelector('.gx-nhead');
  // часть — в одном экране: область без прокрутки (прежняя проверка — П-1 REV-3315-view: ловит переполнение по любой
  // причине). Подменю «Настроек» с 3.3.7.15 («Адреса» в группе «Сервер») было выше области; Д1 его ужал (зазор групп 8,
  // «этап 3» в строку — решение координатора к вопросу V2) — sub: на сколько px его низ (с полем области) ниже области
  const sn = document.querySelector('nav.gx-subnav[data-subnav="settings"]');
  const sub = sn ? Math.ceil(sn.getBoundingClientRect().bottom + parseFloat(getComputedStyle(m).paddingBottom) - m.getBoundingClientRect().bottom) : null;
  return { fit: m.scrollHeight <= m.clientHeight + 1, over: m.scrollHeight - m.clientHeight, sub, groups: g.map((x) => x.dataset.group), soon: g.filter((x) => x.classList.contains('gx-ngrp--soon')).map((x) => x.dataset.group),
    cols: new Set(g.map((x) => Math.round(x.getBoundingClientRect().left))).size,
    sw: document.querySelectorAll('.kk-part .gx-ngrp:not(.gx-ngrp--soon) button.gx-nrow__sw[role="switch"]').length, soonDis: [...document.querySelectorAll('.gx-ngrp--soon button')].every((b) => b.disabled),
    // подпись группы, обрезанная многоточием, — с полным текстом в title (EN «Routing rules and ⚠RU» на 1440)
    cut: [...document.querySelectorAll('.gx-ngrp__t')].filter((t) => t.scrollWidth > t.clientWidth + 1 && t.title !== t.textContent).map((t) => t.textContent),
    rowH: [...new Set([...document.querySelectorAll('.gx-ngrp:not(.gx-ngrp--soon) .gx-nrow')].map((r) => Math.round(r.getBoundingClientRect().height)))],
    swRight: [...document.querySelectorAll('.gx-ngrp:not(.gx-ngrp--soon) .gx-nrow')].every((r) => Math.abs(r.querySelector('.gx-nrow__sw').getBoundingClientRect().right - r.getBoundingClientRect().right) <= 8),
    all: g.filter((x) => !x.classList.contains('gx-ngrp--soon')).map((x) => x.querySelector('.gx-nsw').getAttribute('aria-checked')),
    nhead: h ? { t: h.querySelector('.gx-nhead__t').textContent, s: h.querySelector('.gx-nhead__s').textContent, chip: (h.querySelector('.gx-chip') || {}).textContent || null, all: h.querySelector('.gx-nsw').getAttribute('aria-checked'), info: !!h.querySelector('.gx-nrow__i') } : null,
    bar: /Изменений/.test(document.querySelector('.kk-part').textContent) }; })()`;
out.nt = { a: await js(NT) };
// «все» у группы «Выходы» (смешано) → все вкл; ещё раз → все выкл; правки видны полосой
await js(`document.querySelector('.gx-ngrp[data-group="exits"] .gx-nsw').click(), true`); await sleep(250);
const GX = `(() => { const g = document.querySelector('.gx-ngrp[data-group="exits"]'); return { all: g.querySelector('.gx-nsw').getAttribute('aria-checked'), sw: [...g.querySelectorAll('.gx-nrow__sw')].map((b) => b.getAttribute('aria-checked')), n: g.querySelector('.gx-ngrp__n').textContent, head: document.querySelector('.gx-nhead .gx-nsw').getAttribute('aria-checked'), bar: (document.querySelector('.kk-part .kk-sb__n') || {}).textContent || null }; })()`;
out.nt.on = await js(GX);
await js(`document.querySelector('.gx-ngrp[data-group="exits"] .gx-nsw').click(), true`); await sleep(250);
out.nt.off = await js(GX);
// одна строка вкл → смешано; подпись строки — тоже переключает (label for)
await js(`document.querySelector('.gx-ngrp[data-group="exits"] .gx-nrow__t').click(), true`); await sleep(250);
out.nt.mixed = await js(`document.querySelector('.gx-ngrp[data-group="exits"] .gx-nsw').getAttribute('aria-checked')`);
// «Все» в полосе бота: всё выкл, ещё раз — всё вкл
await js(`document.querySelector('.gx-nhead .gx-nsw').click(), true`); await sleep(250);
out.nt.headOn = await js(`[...document.querySelectorAll('.gx-ngrp:not(.gx-ngrp--soon):not([data-group="how"]) .gx-nrow__sw')].every((b) => b.getAttribute('aria-checked') === 'true')`);
// ⓘ раскрывает пояснение под строкой
out.nt.info = await js(`(() => { const b = document.querySelector('.gx-ngrp .gx-nrow .gx-nrow__i'); const p = document.getElementById(b.getAttribute('aria-controls')); const was = p.hidden; b.click();
  return { was, now: p.hidden, exp: b.getAttribute('aria-expanded'), desc: document.querySelector('[aria-describedby="' + p.id + '"]') ? 1 : 0, text: p.textContent }; })()`);
await js(`(() => { const b = [...document.querySelectorAll('.kk-part button')].find((x) => /Отменить/.test(x.textContent)); if (b) b.click(); return true; })()`); await sleep(400);
out.nt.cancel = await js(NT);
// колонки по ширине окна: 3 на 1100–1999 (UI-A2), 4 на 2000; телефон и планшет — 1. На 1100×900 и 1280×900 часть — в
// одном экране (было +234 и +200 px при 2 колонках), подпись группы целиком (в узкой колонке — переносом, без многоточия)
const COLS = `new Set([...document.querySelectorAll('.gx-ngrp')].map((x) => Math.round(x.getBoundingClientRect().left))).size`;
const FIT = `(() => { const m = document.querySelector('.gx-main'), p = document.querySelector('.kk-part'), r = p.getBoundingClientRect();
  return { over: Math.max(0, Math.ceil(r.bottom + parseFloat(getComputedStyle(m).paddingBottom) - m.getBoundingClientRect().bottom)), cut: [...document.querySelectorAll('.gx-ngrp__t')].filter((t) => t.scrollWidth > t.clientWidth + 1).map((t) => t.textContent) }; })()`;
out.nt.cols = {}; out.nt.fit = {};
for (const [w, h] of [[1100, 900], [1280, 900], [1439, 900], [2000, 1100], [800, 1000]]) { await view(w, h); await sleep(400); out.nt.cols[w] = await js(COLS); if (w >= 1100 && w < 2000) out.nt.fit[w] = await js(FIT); }

// ── «Время»: правка пояса — метка «правки», полоса; «Сохранить» — пояс на узле; 409 при чужой смене — «Применить снова» ──
await view(1440, 900);
await go("#settings?tab=time", 1500);
const TM = `(() => { const s = document.querySelector('#tm-zone'); return { zone: s ? s.value : null, os: s ? s.closest('.gx-field').querySelector('button').disabled : null, dirty: !!document.querySelector('.kk-part .gx-field__l.gx-dirty'),
  bar: (document.querySelector('.kk-part .kk-sb__n') || {}).textContent || null, att: (document.querySelector('.gx-subnav__i[data-part="time"] .gx-att') || {}).textContent || null,
  list: [...document.querySelectorAll('.kk-part .kk-sb__list li')].map((x) => x.textContent), w: Math.round(document.querySelector('.kk-part > .kk-stack').getBoundingClientRect().width),
  desc: (document.querySelector('.kk-part > .kk-phead__s') || {}).textContent || null }; })()`;
out.time = { a: await js(TM) };
await js(`(() => { const s = document.querySelector('#tm-zone'); s.value = 'Europe/Berlin'; s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`); await sleep(300);
out.time.b = await js(TM);
await js(`[...document.querySelectorAll('.kk-part button')].find((x) => x.textContent.trim() === 'Сохранить').click(), true`); await sleep(900);
out.time.c = await js(TM);
out.time.server = await js(`window.KASKAD_MOCK.fetch('/api/settings/time').then((r) => r.json()).then((j) => j.tz)`);
// чужая смена пояса (другая вкладка), пока здесь правят — 409 и «Применить снова»
await js(`(() => { const s = document.querySelector('#tm-zone'); s.value = 'Asia/Tbilisi'; s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`); await sleep(300);
out.time.other = await js(`window.KASKAD_MOCK.fetch('/api/settings/time').then((r) => r.json()).then((j) => window.KASKAD_MOCK.fetch('/api/settings/time', { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-Kaskad-Admin': '1' }, body: JSON.stringify({ tz: 'Europe/Riga', ver: j.ver }) })).then((r) => r.status)`);
await js(`[...document.querySelectorAll('.kk-part button')].find((x) => x.textContent.trim() === 'Сохранить').click(), true`); await sleep(1200);
out.time.d = await js(TM);
await js(`(() => { const b = [...document.querySelectorAll('.kk-part button')].find((x) => /Отменить/.test(x.textContent)); if (b) b.click(); return true; })()`); await sleep(400);

// ── метки внимания: сертификат домена истекает (site=expiring: 12 дней), бот не настроен (после чтения статуса) ──
await view(1440, 1000);
await open("settings?tab=notify", "ru", "&site=expiring");
const ATTS = `Object.fromEntries([...document.querySelectorAll('.gx-subnav__i .gx-att')].map((a) => [a.closest('.gx-subnav__i').dataset.part, a.className.replace('gx-att ', '') + '|' + a.textContent + '|' + (a.closest('.gx-subnav__i').title || '')]))`;
out.att = { a: await js(ATTS) };
await open("settings?tab=notify", "ru", "&bot=ok");
out.att.b = await js(ATTS);

await view(1440, 1000);
out.find = [];
for (const [id, at, need] of items.filter((x) => DOMAIN_NEED.indexOf(x[2]) < 0).concat([["", null, "site=on"]], items.filter((x) => DOMAIN_NEED.indexOf(x[2]) >= 0))) {
  if (need === "site=on") { await open("settings?tab=site", "ru", "&site=on"); continue; }
  await go("#settings", 300);
  await go("#settings" + "?" + new URLSearchParams(at).toString(), at.focus ? 2300 : 1500);
  out.find.push(Object.assign({ id, at }, await js(`(() => { const p = document.querySelector('section[data-page="settings"] .kk-part');
    const f = ${JSON.stringify(at.focus || "")}, n = f && p ? p.querySelector('[data-find="' + f + '"]') : null, ae = document.activeElement;
    return { part: p ? p.dataset.part : null, found: !!n, focused: !!n && (ae === n || n.contains(ae)), hash: location.hash,
      dialog: !!document.querySelector('.gx-dlg-bd:not([hidden]) .gx-dlg, [role="dialog"]') }; })()`)));
  await js(`(() => { document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); return true; })()`); await sleep(250);
}

// ── телефон ──
await view(390, 844);
await open("settings");
out.phone = await js(`({ groups: [...document.querySelectorAll('section[data-page="settings"] .gx-msec__t')].map((x) => x.textContent),
  rows: document.querySelectorAll('section[data-page="settings"] [data-hold-row^="st:"]').length, part: !!document.querySelector('.kk-part'), nav: !!document.querySelector('.gx-subnav') })`);
await go("#settings?tab=notify", 1500);
out.phone.nt = await js(`(() => { const g = [...document.querySelectorAll('.gx-ngrp')]; const tg = g[0].querySelector('.gx-ngrp__tg'); const l = g[0].querySelector('.gx-ngrp__l'); const was = l.hidden; tg.click();
  const rowH = Math.round(l.querySelector('.gx-nrow').getBoundingClientRect().height);
  return { n: g.length, was, now: l.hidden, exp: tg.getAttribute('aria-expanded'), allH: Math.round(g[0].querySelector('.gx-nsw').getBoundingClientRect().height), rowH,
    hscroll: document.documentElement.scrollWidth > innerWidth, cols: new Set(g.map((x) => Math.round(x.getBoundingClientRect().left))).size }; })()`);
await go("#settings", 1200);
await js(`document.querySelector('[data-hold-row="st:gateway"]').click(), true`); await sleep(800);
out.phone.row = await js(`({ hash: location.hash, title: (document.querySelector('.kk-mhead__t') || {}).textContent })`);
await js(`document.querySelector('.gx-mnav button').click(), true`); await sleep(800);
out.phone.back = await js(`({ hash: location.hash, rows: document.querySelectorAll('section[data-page="settings"] [data-hold-row^="st:"]').length })`);

// ── EN ──
await view(1440, 1000);
await open("settings?tab=gateway", "en");
out.en = await js(`({ label: document.querySelector('nav.gx-subnav').getAttribute('aria-label'), groups: [...document.querySelectorAll('.gx-subnav__h')].map((h) => h.textContent),
  crumb: [...document.querySelectorAll('#head .gx-crumb--path > span')].map((x) => x.textContent), h1: document.querySelector('#head .gx-h1').textContent })`);

// EN на 1440: подписи групп длиннее («Routing rules and ⚠RU») — обрезанная подпись несёт полный текст в title
await view(1440, 900);
await open("settings?tab=notify", "en", "&bot=ok");
await sleep(600);
out.nt.en = await js(`(() => { const t = [...document.querySelectorAll('.gx-ngrp__t')]; return { n: t.length, cutAll: t.filter((x) => x.scrollWidth > x.clientWidth + 1).length,
  cut: t.filter((x) => x.scrollWidth > x.clientWidth + 1 && x.title !== x.textContent).map((x) => x.textContent) }; })()`);

console.log(JSON.stringify(out));
process.exit(0);
