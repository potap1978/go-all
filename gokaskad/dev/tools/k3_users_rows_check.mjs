// k3_users_rows_check.mjs — «Пользователи и устройства»: строки по финалу 01b Claude Design «Мягкая линия, скруглённый
// последний узел» (владелец 24.10; HANDOFF в «Users Rows final.dc.html») в настоящем Chrome на заглушке панели.
// Что замеряется (tests/test_users_rows_01b.py читает JSON из stdout):
//   geo.<плотность>-<окно> — группа «Мама» (2 устройства) раскрыта: высоты строк, сетка колонок, X центра аватара и линии
//              дерева, начало линии под аватаром, X значка устройства и имени пользователя, X и ширина линии активности
//              у пользователя и устройств, объём рядом, шрифты имени / платформы, счётчик, подложка группы, разделители;
//              кружок скрыт (is-cc-tight) — линия от левого края имени (anc), конец отвода к значку (elb);
//   hover    — наведение на последнее устройство: его ветка (отрезок от аватара и скруглённый узел) цветом --text-3;
//   chips    — «Строка»: метка состояния короче словом, полный текст — в подсказке; в «Средне» — полный;
//   wide     — 2560: таблица не шире 2000, «Протоколы» без «+N», «Маршрут» целиком;
//   phone    — телефон 390: «N из M» со стрелкой раскрывает устройства (дерево, строка ≥ 44), нажатие на устройство —
//              его лист, на имя — лист пользователя; раскрытие переживает живую перерисовку и перезагрузку;
//   en       — EN: подписи раскрытия и метки «Строки» без русского.
// node tools/k3_users_rows_check.mjs <адрес статики панели> <chrome>
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
const prof = mkdtempSync(join(tmpdir(), "k3rows-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,900", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_users_rows_check: время вышло"); process.exit(3); }, 240000).unref();
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
const view = (w, h) => cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
// still=1 — «в сети» устройств не меняется само; плотность — в браузере (как выбрал человек), раскрытое — в сессии
async function open(w, h, dn, lang, sel) {
  await view(w, h);
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&still=1&lang=${lang || "ru"}#people` });
  await until(`!!window.KASKAD_MOCK && !!document.querySelector('section[data-page="people"]:not([hidden])')`, 20000);
  await js(`(sessionStorage.clear(), localStorage.setItem("kaskad-density-people", ${JSON.stringify(dn || "md")}), true)`);
  await cdp("Page.reload"); await sleep(300);
  await until(`!!document.querySelector(${JSON.stringify(sel || ".gx-xt__g")})`, 20000);
  await sleep(500);
}
const G = (name) => `[...document.querySelectorAll('.gx-xt__g')].find((g) => g.querySelector('.kk-pname').textContent === ${JSON.stringify(name)})`;
const openGroup = (name) => js(`(() => { const g = ${G(name)}; if (!g.classList.contains('is-open')) g.querySelector('.gx-xt__x').click(); return true; })()`);
// замер группы: X — от левого края строки
const GEO = (name) => `(() => {
  const g = ${G(name)}, rows = [...g.querySelectorAll('.gx-xt__r')], o0 = rows[0].getBoundingClientRect();
  const X = (e) => (e && e.getClientRects().length ? Math.round(e.getBoundingClientRect().left - o0.left) : null);
  const W = (e) => (e && e.getClientRects().length ? Math.round(e.getBoundingClientRect().width) : null);
  const cs = (e) => getComputedStyle(e), vis = (e) => !!e && e.getClientRects().length > 0 && cs(e).display !== 'none';
  const t3 = cs(document.querySelector('.gx-xt__r--sub .gx-xt__os')).color;
  // начало линии — видимый кружок (центр); кружок скрыт (is-cc-tight, REV-it1-pol П-1) — левый край блока имени: линия
  // левым краем по нему (X — её середина), на 3 px ниже блока
  const avE = rows[0].querySelector('.gx-xt__av'), nbE = rows[0].querySelector('.gx-xt__nb'), tu = rows[0].querySelector('.gx-xt__tr--u');
  const av = avE.getBoundingClientRect(), avOn = vis(avE), ancE = (avOn ? avE : nbE).getBoundingClientRect();
  const tr = (r) => { const t = r.querySelector('.gx-xt__tr'); if (!vis(t)) return null; const b = t.getBoundingClientRect(); return Math.round((b.left - o0.left + parseFloat(cs(t).borderLeftWidth) / 2) * 2) / 2; };
  const sp = (r) => [...r.querySelectorAll('.gx-spark')].find(vis);
  const vol = (r) => [...r.querySelectorAll('.kk-act__v, .kk-actl__t')].find(vis);
  const tb = g.closest('.gx-xt'), part = g.closest('.kk-part');
  return {
    tbw: Math.round(tb.getBoundingClientRect().width), partw: Math.round(part.getBoundingClientRect().width),
    cols: cs(rows[0]).gridTemplateColumns, gap: cs(rows[0]).columnGap,
    h: rows.map((r) => Math.round(r.getBoundingClientRect().height)),
    tight: tb.classList.contains('is-cc-tight'), avOn,
    avc: avOn ? Math.round((av.left + av.width / 2 - o0.left) * 2) / 2 : null, tr: rows.map(tr),
    anc: avOn ? Math.round((av.left + av.width / 2 - o0.left) * 2) / 2 : Math.round((ancE.left - o0.left + parseFloat(cs(tu).borderLeftWidth) / 2) * 2) / 2,
    trTop: vis(tu) ? Math.round(tu.getBoundingClientRect().top - ancE.bottom) : null,
    // конец отвода к значку устройства (узел — край рамки, остальные — ::after)
    elb: rows.slice(1).map((r) => { const t = r.querySelector('.gx-xt__tr'), b = t.getBoundingClientRect(); if (t.classList.contains('gx-xt__tr--end')) return Math.round(b.right - o0.left);
      const a = getComputedStyle(t, '::after'); return Math.round(b.left + parseFloat(cs(t).borderLeftWidth) + parseFloat(a.left) + parseFloat(a.width) - o0.left); }),
    trTone: vis(tu) ? cs(tu).borderLeftColor : null, border: cs(document.querySelector('.gx-xt')).borderTopColor,
    end: rows.slice(1).map((r) => r.querySelector('.gx-xt__tr').classList.contains('gx-xt__tr--end')),
    endRad: cs(rows[rows.length - 1].querySelector('.gx-xt__tr')).borderBottomLeftRadius,
    gapNext: Math.round(rows[2].getBoundingClientRect().top - rows[1].getBoundingClientRect().bottom),
    nameX: X(rows[0].querySelector('.gx-xt__n')), devX: rows.slice(1).map((r) => X(r.querySelector('.gx-xt__dev'))),
    sp: rows.map((r) => { const s = sp(r); return s ? [X(s), W(s)] : null; }),
    vol: rows.map((r) => { const v = vol(r); return v ? [X(v), W(v), cs(v).fontVariantNumeric] : null; }),
    nF: [cs(rows[0].querySelector('.gx-xt__n')).fontWeight, cs(rows[0].querySelector('.gx-xt__n')).fontSize, cs(rows[0].querySelector('.gx-xt__n')).lineHeight],
    dF: [cs(rows[1].querySelector('.gx-xt__n')).fontWeight, cs(rows[1].querySelector('.gx-xt__n')).fontSize, cs(rows[1].querySelector('.gx-xt__n')).lineHeight],
    osF: [cs(rows[1].querySelector('.gx-xt__os')).fontSize, cs(rows[1].querySelector('.gx-xt__os')).color === t3, vis(rows[1].querySelector('.gx-xt__os'))],
    osBelow: Math.round(rows[1].querySelector('.gx-xt__os').getBoundingClientRect().top - rows[1].querySelector('.gx-xt__n').getBoundingClientRect().bottom),
    ucp: (() => { const u = rows[0].querySelector('.kk-ucp'); return u ? [u.textContent, u.title, Math.round(u.getBoundingClientRect().height), cs(u).backgroundColor === cs(document.querySelector('.gx-xt__x')).color ? 'fg' : cs(u).color] : null; })(),
    gbg: cs(g).backgroundColor, rbg: cs(rows[0]).backgroundColor, subShadow: rows.slice(1).map((r) => cs(r).boxShadow),
    devIco: W(rows[1].querySelector('.gx-xt__dev svg')),
    // метки «Состояния», которые не входят в колонку (полный текст — в подсказке, но на узкой таблице — короче словом)
    stCut: [...tb.querySelectorAll('.gx-xt__nl > .gx-chip')].filter(vis).filter((c) => c.scrollWidth > c.clientWidth + 1 && !c.title).map((c) => c.textContent),
    stOut: [...tb.querySelectorAll('.gx-xt__nl > .gx-chip')].filter(vis).filter((c) => c.getBoundingClientRect().right > c.closest('.gx-xt__x').getBoundingClientRect().right + 0.5).map((c) => c.textContent),
    // «доступа нет» в «Протоколах» — без подсказки: обрезать нельзя (на узкой таблице — второй строкой)
    mut: (() => { const m = [...tb.querySelectorAll('.gx-xt__tags > .gx-xt__mut')].filter(vis);
      return [m.length, m.filter((e) => e.getBoundingClientRect().right > e.parentElement.getBoundingClientRect().right + 0.5).map((e) => e.textContent)]; })(),
  };
})()`;
const out = { geo: {} };
// ── геометрия: три плотности на 1440 (таблица 916 — кадры эталона), «Средне» на 2560, 1100 и 2000 ──
for (const [dn, w, h] of [["md", 1440, 900], ["lg", 1440, 900], ["row", 1440, 900], ["md", 2560, 1440], ["md", 2000, 1100], ["md", 1100, 900], ["lg", 1100, 900], ["lg", 2560, 1440]]) {
  await open(w, h, dn);
  await openGroup("Мама"); await sleep(300);
  out.geo[dn + "-" + w] = await js(GEO("Мама"));
}
// ── наведение на последнее устройство «Мамы»: его ветка цветом --text-3, у «Папы» — нет ──
{
  await open(1440, 900, "md");
  await openGroup("Мама"); await openGroup("Папа"); await sleep(300);
  const pos = await js(`(() => { const r = [...(${G("Мама")}).querySelectorAll('.gx-xt__r--sub')].pop().getBoundingClientRect(); return { x: r.left + r.width * 0.6, y: r.top + r.height / 2 }; })()`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: pos.x, y: pos.y }); await sleep(300);
  out.hover = await js(`(() => { const t3 = getComputedStyle(document.querySelector('.gx-xt__r--sub .gx-xt__os')).color, c = (e, p) => getComputedStyle(e)[p];
    const m = ${G("Мама")}, p = ${G("Папа")};
    return { t3, u: c(m.querySelector('.gx-xt__tr--u'), 'borderLeftColor'), end: c(m.querySelector('.gx-xt__tr--end'), 'borderBottomColor'),
      prev: c(m.querySelector('.gx-xt__r--sub .gx-xt__tr'), 'borderLeftColor'), prevElbow: c(m.querySelector('.gx-xt__r--sub .gx-xt__tr'), 'borderTopColor'),
      other: c(p.querySelector('.gx-xt__tr--u'), 'borderLeftColor'), bg: c([...m.querySelectorAll('.gx-xt__r--sub')].pop(), 'backgroundColor') }; })()`);
  // ::after (отвод) соседнего устройства — своим цветом, не цветом ветки
  out.hover.prevAfter = await js(`getComputedStyle((${G("Мама")}).querySelector('.gx-xt__r--sub .gx-xt__tr'), '::after').borderTopColor`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseMoved", x: 5, y: 5 }); await sleep(200);
  out.hover.after = await js(`getComputedStyle((${G("Мама")}).querySelector('.gx-xt__tr--u')).borderLeftColor`);
}
// ── метки «Состояния»: «Средне» — полностью, «Строка» — короче словом, полный текст — в подсказке ──
for (const lang of ["ru", "en"]) {
  const r = {};
  for (const dn of ["md", "row"]) {
    await open(1440, 900, dn, lang);
    await openGroup(lang === "en" ? "Mom" : "Мама"); await sleep(300);
    r[dn] = await js(`[...document.querySelectorAll('.gx-xt__nl > .gx-chip')].filter((c) => c.getClientRects().length).map((c) => { const a = getComputedStyle(c, '::after').content; return { text: (a && a !== 'none' && a !== 'normal' ? a.replace(/^"|"$/g, '') : '') + c.innerText.trim(), title: c.title, full: c.textContent, cut: c.scrollWidth > c.clientWidth + 1 }; })`);
  }
  out["chips_" + lang] = r;
}
// ── 2560: таблица ≤ 2000, «Протоколы» без «+N», «Маршрут» целиком ──
{
  await open(2560, 1440, "md");
  await openGroup("Мама"); await sleep(400);
  out.wide = await js(`(() => { const tb = document.querySelector('.gx-xt--k1');
    return { tbw: Math.round(tb.getBoundingClientRect().width), more: [...tb.querySelectorAll('.gx-xt__more')].filter((m) => !m.hidden && m.getClientRects().length).length,
      cutRoute: [...tb.querySelectorAll('.kk-rt .gx-xt__t')].filter((e) => e.getClientRects().length && e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent),
      subLeft: Math.round(document.querySelector('.kk-sub .gx-subnav, nav[data-subnav]').getBoundingClientRect().right), partLeft: Math.round(document.querySelector('#part-people').getBoundingClientRect().left) }; })()`);
}
// ── телефон 390: раскрытие «N из M» со стрелкой, дерево, листы, память раскрытия ──
{
  await open(390, 844, "md", "ru", ".kk-pg");
  const P = `[...document.querySelectorAll('.kk-pg')].find((g) => g.querySelector('.kk-prow__n').textContent === 'Мама')`;
  const r = {};
  r.before = await js(`(() => { const g = ${P}, b = g.querySelector('.kk-prow__exp'); return { exp: b.getAttribute('aria-expanded'), hidden: g.querySelector('.kk-pdevs').hidden, ctl: b.getAttribute('aria-controls') === g.querySelector('.kk-pdevs').id, label: b.getAttribute('aria-label'), ucp: g.querySelector('.kk-ucp').textContent, more: !!g.querySelector('.kk-prow__more') }; })()`);
  r.noDevs = await js(`(() => { const g = [...document.querySelectorAll('.kk-pg')].find((x) => !x.querySelector('.kk-prow__exp')); return g ? { ucp: !!g.querySelector('.kk-ucp'), on: !!g.querySelector('.kk-prow__on') } : null; })()`);
  await js(`(${P}).querySelector('.kk-prow__exp').click(), true`); await sleep(300);
  r.open = await js(`(() => { const g = ${P}, av = g.querySelector('.kk-prow .kk-av, .kk-prow [class*="av"]').getBoundingClientRect(), row = g.querySelector('.kk-prow').getBoundingClientRect();
    const tu = g.querySelector('.gx-xt__tr--u'), ds = [...g.querySelectorAll('.kk-mdev')];
    return { exp: g.querySelector('.kk-prow__exp').getAttribute('aria-expanded'), shown: g.querySelector('.kk-pdevs').offsetHeight > 0, devs: ds.length,
      h: ds.map((d) => Math.round(d.getBoundingClientRect().height)), rowH: Math.round(row.height),
      avc: Math.round((av.left + av.width / 2 - row.left) * 2) / 2, tu: getComputedStyle(tu).display !== 'none' ? Math.round((tu.getBoundingClientRect().left - row.left + 1) * 2) / 2 : null,
      end: ds.map((d) => d.querySelector('.gx-xt__tr').classList.contains('gx-xt__tr--end')), ic: ds.map((d) => Math.round(d.querySelector('.kk-mdev__ic').getBoundingClientRect().left - row.left)),
      os: ds.map((d) => d.querySelector('.kk-mdev__os').textContent), st: ds.map((d) => d.querySelector('.kk-prow__on') ? d.querySelector('.kk-prow__on').textContent : null) }; })()`);
  // нажатие на устройство — его лист
  r.devName = await js(`(${P}).querySelector('.kk-mdev__n').textContent`);
  await js(`(${P}).querySelector('.kk-mdev').click(), true`);
  await until(`!!document.querySelector('.gx-bsheet')`, 4000); await sleep(300);
  r.devSheet = await js(`(document.querySelector('.gx-bsheet h2, .gx-bsheet [class*="title"]') || {}).textContent || ''`);
  await js(`(document.querySelector('.gx-bsheet [aria-label="Закрыть"], .gx-bsheet .gx-bsheet__x, .gx-bsheet button[class*="close"]') || { click() { history.back(); } }).click(), true`);
  await until(`!document.querySelector('.gx-bsheet')`, 4000).catch(() => null); await sleep(400);
  // перерисовка живыми данными (мок шлёт кадры: объём за сутки растёт с открытым окном) — ждём, пока группа «Мамы»
  // станет новым узлом, затем перезагрузка — раскрыто на месте
  await js(`((${P}).__k3old = 1, true)`);
  r.rerendered = await until(`!(${P}).__k3old`, 15000).then(() => true, () => false);
  r.afterLive = await js(`(() => { const g = ${P}; return [g.classList.contains('is-open'), g.querySelector('.kk-prow__exp').getAttribute('aria-expanded'), g.querySelector('.kk-pdevs').hidden]; })()`);
  await cdp("Page.reload"); await until(`!!document.querySelector('.kk-pg')`, 20000); await sleep(500);
  r.afterReload = await js(`(() => { const g = ${P}; return [g.classList.contains('is-open'), g.querySelector('.kk-prow__exp').getAttribute('aria-expanded'), g.querySelector('.kk-pdevs').hidden]; })()`);
  // нажатие на имя — лист пользователя (раскрытие не меняется)
  await js(`(${P}).querySelector('.kk-prow__main').click(), true`);
  await until(`!!document.querySelector('.gx-bsheet')`, 4000); await sleep(300);
  r.userSheet = await js(`(document.querySelector('.gx-bsheet h2, .gx-bsheet [class*="title"]') || {}).textContent || ''`);
  r.stillOpen = await js(`(${P}).classList.contains('is-open')`);
  out.phone = r;
}
// ── EN: подписи раскрытия на телефоне ──
{
  await open(390, 844, "md", "en", ".kk-pg");
  out.en = await js(`[...document.querySelectorAll('.kk-prow__exp')].map((b) => b.getAttribute('aria-label')).concat([...document.querySelectorAll('.kk-ucp')].map((u) => u.title))`);
}
console.log(JSON.stringify(out));
process.exit(0);
