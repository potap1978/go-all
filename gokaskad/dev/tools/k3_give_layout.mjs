// k3_give_layout.mjs — раскладка окна выдачи (give.js/give.css) в настоящем Chrome без окна (3.3.3 №38, R-333-qr ≤2):
// панель (?mock=1) и мастер (шаг 6, ?mock=1&step=done) на ширинах ноутбука и телефона. Для каждого состояния:
//   frameOut   — на сколько px рамка QR выходит за свой блок (.gs-key без полей) или за .gs;
//   covered    — точки рамки, которые закрыты чужим узлом (колонка видео мастера и т. п.);
//   modulePx   — модуль кода в рамке (порог: компьютер 4, телефон 3.6);
//   cols       — колонки рядом или друг под другом во всю ширину (wrapBad — перенесена, но не растянута);
//   seen       — QR и «Часть N из M» целиком видны (без прокрутки руками);
//   scr        — окно никто не прокручивал сам (при открытии, смене платформы, протокола и приложения — 0);
//   selSeen    — строки «Платформа / Протокол» на виду; partsSeen — «Часть N из M» на виду; frameTop — верх рамки на виду;
//   whole      — кнопка «Показать QR целиком» видна (код не помещается — прокрутка только по ней, R-333-qr ≤2: повтор №2).
// node tools/k3_give_layout.mjs <адрес статики панели> <chrome>  → JSON в stdout
import { spawn } from "node:child_process";
import { readFileSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3];
// порт отладки выбирает сам Chrome (0 → свободный) и пишет его в DevToolsActivePort профиля: два набора разом
// не попадут в один порт и не подключатся к чужому браузеру (tools/k3_unit_par.py гоняет модули параллельно)
const prof = mkdtempSync(join(tmpdir(), "k3lay-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) {} } try { rmSync(prof, { recursive: true, force: true }); } catch (e) {} };
process.on("exit", stop);
setTimeout(() => { console.error("k3_give_layout: время вышло"); process.exit(3); }, 260000).unref();
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
async function target() {
  for (let i = 0; i < 100; i++) {
    try { const port = Number(readFileSync(join(prof, "DevToolsActivePort"), "utf8").split("\n")[0]); const l = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); const p = l.find((x) => x.type === "page"); if (p) return p.webSocketDebuggerUrl; } catch (e) {}
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
const view = (w, h) => cdp("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
const gk = (k) => js(`(() => { const e = document.querySelector('[data-gk="${k}"]'); if (!e) return false; e.click(); return true; })()`);
const settle = () => sleep(450);                       // ResizeObserver → кадр → перерисовка → прокрутка

// что на экране сейчас (в странице): см. шапку файла
const PROBE = `(() => {
  const r2 = (b) => b ? { l: Math.round(b.left), t: Math.round(b.top), r: Math.round(b.right), b: Math.round(b.bottom), w: Math.round(b.width) } : null;
  const gs = document.querySelector('.gs'); if (!gs) return { none: true };
  // целиком ли узел (или полоса [t, b] его высоты) в окне и в каждом предке с прокруткой
  const inView = (e, t0, b0) => {
    if (!e) return false;
    const b = e.getBoundingClientRect(); if (!(b.height > 0)) return false;
    const t = t0 === undefined ? b.top : t0, z = b0 === undefined ? b.bottom : b0;
    if (t < -0.5 || z > innerHeight + 0.5) return false;
    for (let p = e.parentElement; p; p = p.parentElement) {
      const o = getComputedStyle(p).overflowY;
      if (o === 'auto' || o === 'scroll' || o === 'hidden') { const pb = p.getBoundingClientRect(); if (t < pb.top - 0.5 || z > pb.bottom + 0.5) return false; }
    }
    return true;
  };
  const phone = gs.getAttribute('data-phone') === '1';
  const out = { vw: innerWidth, vh: innerHeight, phone, gsW: Math.round(gs.getBoundingClientRect().width) };
  out.scr = Math.max(0, ...[...document.querySelectorAll('*')].map((e) => e.scrollTop || 0));
  const chips = [...gs.querySelectorAll('.gs-sel .gs-chips')];
  out.selSeen = chips.length ? chips.every((e) => inView(e)) : null;
  const wb = gs.querySelector('[data-gk="qrwhole"]');
  out.whole = !!wb && !wb.hidden && !wb.hasAttribute('data-off') && inView(wb);
  const l = gs.querySelector('.gs-col--l'), r = gs.querySelector('.gs-col--r');
  if (l && r) {
    const lb = l.getBoundingClientRect(), rb = r.getBoundingClientRect(), gb = gs.getBoundingClientRect();
    const side = Math.abs(lb.top - rb.top) < 2 && rb.left > lb.right;
    out.cols = side ? 'side' : 'stack';
    out.wrapBad = !side && (Math.abs(rb.width - gb.width) > 1 || Math.abs(rb.left - gb.left) > 1);
  }
  const fr = gs.querySelector('.gs-qr__frame'), d = gs.querySelector('.gs-dense');
  out.dense = !!d; out.denseT = d ? (d.querySelector('.gs-dense__t') || {}).textContent : null;
  out.denseX = d ? (d.querySelector('.gs-dense__x') || {}).textContent : null;
  // всё в блоке ключа (рамка, кнопки «Скопировать ключ / Скачать .conf», пометка) — внутри его полей
  const kk = gs.querySelector('.gs-key');
  if (kk) {
    const kb0 = kk.getBoundingClientRect(), c0 = getComputedStyle(kk), a = kb0.left + parseFloat(c0.paddingLeft) - 0.5, z = kb0.right - parseFloat(c0.paddingRight) + 0.5;
    out.keyOut = Math.round(Math.max(0, ...[...kk.querySelectorAll('.gs-keybtns > *, .gs-qr__frame, .gs-dense, .gs-dense__warn')].filter((e) => e.getBoundingClientRect().width > 0).map((e) => { const b = e.getBoundingClientRect(); return Math.max(a - b.left, b.right - z); })));
  }
  if (!fr || fr.getBoundingClientRect().width === 0) return out;
  const key = fr.closest('.gs-key'), kb = key.getBoundingClientRect(), cs = getComputedStyle(key), fb = fr.getBoundingClientRect(), gb = gs.getBoundingClientRect();
  const inL = kb.left + parseFloat(cs.paddingLeft), inR = kb.right - parseFloat(cs.paddingRight);
  out.frame = r2(fb); out.keyInner = Math.round(inR - inL);
  out.frameOut = Math.max(0, Math.round(Math.max(inL - fb.left, fb.right - inR, gb.left - fb.left, fb.right - gb.right)));
  const svg = fr.querySelector('.kq__part:not([hidden]) svg') || fr.querySelector('svg');
  const vb = svg && svg.getAttribute('viewBox') ? svg.getAttribute('viewBox').split(' ').map(Number) : null;
  out.modulePx = vb ? +((fb.width - 16) / vb[2]).toFixed(2) : null;
  const q = fr.closest('.gs-qr'), qb = q.getBoundingClientRect(), parts = q.querySelector('.gs-parts');
  // видно ли целиком: внутри окна и внутри каждого предка с прокруткой
  out.seen = inView(q) && qb.left >= -0.5 && qb.right <= innerWidth + 0.5; out.parts = parts ? parts.textContent : null;
  out.partsSeen = parts ? inView(parts) : null;
  out.frameTop = inView(fr, fb.top, fb.top + 24);
  out.partsAbove = parts ? parts.getBoundingClientRect().bottom <= fb.top + 0.5 : null;
  return out;
})()`;
// закрыт ли угол рамки чужим узлом: рамку — в середину видимой области, точки по углам и в центре
const COVER = `(async () => {
  const fr = document.querySelector('.gs .gs-qr__frame'); if (!fr || !fr.getBoundingClientRect().width) return null;
  // прокрутку замера вернуть: следующие замеры смотрят, не двигает ли окно сам код (scr)
  const keep = [...document.querySelectorAll('*')].filter((e) => e.scrollTop > 0).map((e) => [e, e.scrollTop]);
  fr.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
  await new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(ok)));
  const b = fr.getBoundingClientRect(), i = 6, pts = [[b.left + i, b.top + i], [b.right - i, b.top + i], [b.left + i, b.bottom - i], [b.right - i, b.bottom - i], [(b.left + b.right) / 2, (b.top + b.bottom) / 2]];
  const n = pts.filter(([x, y]) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight).filter(([x, y]) => { const e = document.elementFromPoint(x, y); return !e || !fr.contains(e); }).length;
  document.querySelectorAll('*').forEach((e) => { if (e.scrollTop > 0) e.scrollTop = 0; });
  keep.forEach(([e, t]) => { e.scrollTop = t; });
  await new Promise((ok) => requestAnimationFrame(ok));
  return n;
})()`;
const res = [];
async function probe(where, extra) {
  await sleep(150);                                   // кнопка «Показать QR целиком» — после отрисовки (setTimeout)
  const p = await js(PROBE);
  p.where = where; Object.assign(p, extra || {});
  if (p.frame) p.covered = await js(COVER);
  res.push(p);
}

async function panel(w, h) {
  await view(w, h);
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);   // тот же адрес с # — без перезагрузки: окно было бы вторым
  await cdp("Page.navigate", { url: `${BASE}/index.html?mock=1&lang=ru#people` });
  await sleep(500);
  if (w < 1100) {
    // телефон и планшет (UI-B 23.10, эталон Т1–Т3): строка «Мама» → лист человека → «Телефон Мамы» → лист устройства →
    // «Выдать настройки» (протокол выбирается в окне выдачи)
    await until(`[...document.querySelectorAll('.kk-plist .kk-prow__n')].some((x) => /^(Мама|Mom)$/.test(x.textContent))`);
    await js(`([...document.querySelectorAll('.kk-plist .kk-prow')].find((x) => /^(Мама|Mom)$/.test(x.querySelector('.kk-prow__n').textContent)).querySelector('.kk-prow__main').click(), true)`);
    await until(`[...document.querySelectorAll(".gx-bsheet--act .gx-mrow[data-hold-row^='dev:']")].some((x) => /Мамы|Mom/.test(x.textContent))`);
    await js(`([...document.querySelectorAll(".gx-bsheet--act .gx-mrow[data-hold-row^='dev:']")].find((x) => /Телефон Мамы|Mom/.test(x.textContent)).click(), true)`);
    await until(`!!document.querySelector('.gx-bsheet--act .gx-mrow--nav')`);
    await js(`(document.querySelector('.gx-bsheet--act .gx-mrow--nav').click(), true)`);
    // QR → выбор «Подписка | Статический ключ» (владелец 24.10): окно выдачи — «Статический ключ»
    await until(`!!document.querySelector('[data-qr-pick="static"]')`);
    await js(`(document.querySelector('[data-qr-pick="static"]').click(), true)`);
  } else {
    await until(`!!document.querySelector('[aria-label*="AmneziaWG 3.1"]')`);
    // UI-4: таблица К1 — раскрыть человека, плашка AWG 3.1 в строке его устройства
    await js(`(() => { const r = [...document.querySelectorAll('.gx-xt__r[data-did]')].find((x) => /Мамы|Mom/.test(x.textContent)); const g = r.closest('.gx-xt__g'); if (!g.classList.contains('is-open')) g.querySelector('.gx-xt__x').click(); return true; })()`);
    await js(`(() => { const b = [...document.querySelectorAll('button')].find((x) => /AmneziaWG 3\\.1/.test(x.getAttribute('aria-label') || '') && x.closest('.gx-xt__r[data-did]') && /Мамы|Mom/.test(x.closest('.gx-xt__r').textContent)); b.click(); return true; })()`);
  }
  await until(`!!document.querySelector('.gs .gs-step') && !document.querySelector('.gs-host[aria-busy]')`);
  await settle();
}
async function wizard(w, h) {
  await view(w, h);
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(100);
  await cdp("Page.navigate", { url: `${BASE}/setup/index.html?mock=1&step=done&theme=dark&lang=ru` });
  await sleep(900);
  await js(`(() => { const b = [...document.querySelectorAll("button")].find((x) => x.textContent.trim() === "Скрыть"); if (b) b.click(); return true; })()`);
  await until(`!!document.querySelector('[data-k="issue"]')`, 20000);
  const s0 = await js(`(document.getElementById('qs-scroll') || {}).scrollTop || 0`);
  await js(`(() => { document.querySelector('[data-k="issue"]').click(); return true; })()`);
  await until(`!!document.querySelector('.qs-give .gs .gs-step')`, 20000);
  await settle();
  return s0;
}

await cdp("Page.enable"); await cdp("Runtime.enable");
// панель: открыть на каждой ширине (порог считается при открытии) + смена ширины у открытого окна
for (const w of [1000, 1024, 1080, 1100, 1150, 1240, 1300]) { await panel(w, 800); await probe("panel-open", { app: "defaultvpn" }); }
for (const w of [1050, 990, 1200]) { await view(w, 800); await settle(); await probe("panel-resize", { app: "defaultvpn" }); }
// высота ноутбука: открыли — окно не прокручено, выбор и «Часть 1 из 2» на виду; QR целиком не помещается — кнопка
// «Показать QR целиком»; смена протокола, платформы и приложения окно не двигает; нажали кнопку — QR целиком (повтор №2)
const busy = async () => { await sleep(250); await until(`!document.querySelector('.gs-host[aria-busy]')`); await sleep(600); };
for (const [w, h] of [[1366, 657], [1280, 689]]) {
  await panel(w, h); await probe("panel-short", { app: "defaultvpn" });
  for (const k of ["proto:awg2", "plat:android", "plat:ios", "proto:awg3"]) { await gk(k); await busy(); await probe("panel-short-chip", { chip: k }); }
  await gk("app:amneziawg"); await settle(); await gk("app:defaultvpn"); await settle(); await probe("panel-short-app", { app: "defaultvpn" });
  await gk("qrwhole"); await settle(); await probe("panel-short-whole", { app: "defaultvpn" });
  // перерисовка (другое приложение и назад) не двигает код: кнопка держит место, прокрутка окна та же (третий круг №3, №4);
  // сразу после перерисовки, до таймеров, кнопка уже в прошлом состоянии — погашена, место занято
  const sync = await js(`(() => { document.querySelector('[data-gk="app:amneziawg"]').click(); document.querySelector('[data-gk="app:defaultvpn"]').click();
    const b = document.querySelector('[data-gk="qrwhole"]'), f = document.querySelector('.gs .gs-qr__frame');
    return b ? { hidden: b.hidden, off: b.hasAttribute('data-off'), top: f ? Math.round(f.getBoundingClientRect().top) : null } : null; })()`);
  await settle(); await gk("app:amneziawg"); await settle(); await gk("app:defaultvpn"); await settle();
  await probe("panel-short-rerender", { app: "defaultvpn", sync, top0: res[res.length - 1].frame ? res[res.length - 1].frame.t : null, scr0: res[res.length - 1].scr });
  // прокрутили руками назад — код снова срезан, кнопка снова горит
  await js(`(() => { document.querySelectorAll('*').forEach((e) => { if (e.scrollTop > 0) e.scrollTop = 0; }); return 1; })()`); await settle();
  await probe("panel-short-back", { app: "defaultvpn" });
}
// высота окна сменилась у открытого окна (развернули окно, закрыли DevTools): кнопка сверяется с окном (третий круг №1)
await panel(1366, 657); await view(1366, 1000); await settle(); await probe("panel-taller", { app: "defaultvpn" });
await view(1366, 657); await settle(); await probe("panel-lower", { app: "defaultvpn" });
await panel(1366, 1000); await probe("panel-tall", { app: "defaultvpn" });
await view(1366, 657); await settle(); await probe("panel-lower", { app: "defaultvpn" });
// мастер (с 3.3.3 без колонки видео — ссылка «▶ Видео — скоро» в шапке): шаг во всю колонку, QR в полной рамке на
// 1100–1440 (повтор №1); окно после «Выдать» не прокручено
for (const w of [1100, 1150, 1200, 1240, 1280, 1300, 1440, 1441, 1600]) { const s0 = await wizard(w, 800); await probe("wizard-open", { app: "defaultvpn", scr0: s0 }); }
for (const w of [1160, 1260, 1500]) { await view(w, 800); await settle(); await probe("wizard-resize", { app: "defaultvpn" }); }
// компьютер уже 1100
for (const w of [800, 1024]) { const s0 = await wizard(w, 800); await probe("wizard-open", { app: "defaultvpn", scr0: s0 }); }
// узкое место (блок выдачи сжат искусственно): свёрнут, «Расширьте окно…»; «Всё равно показать» — рамка в блоке
for (const w of [1150, 1440]) {
  await wizard(w, 800);
  await js(`(() => { const s = document.createElement('style'); s.textContent = '.qs-give { max-width: 380px; }'; document.head.append(s); return true; })()`);
  await settle(); await probe("wizard-squeezed", { app: "defaultvpn" });
  await gk("denseshow"); await settle(); await probe("wizard-anyway", { app: "defaultvpn" });
}
for (const [w, h] of [[1366, 657], [1280, 689]]) {
  const s0 = await wizard(w, h); await probe("wizard-short", { app: "defaultvpn", scr0: s0 });
  await gk("qrwhole"); await settle(); await probe("wizard-short-whole", { app: "defaultvpn" });
}
// клавиатура: Enter на «Показать QR целиком» — кнопка гаснет, фокус на видимом узле и с контуром (четвёртый круг №1);
// другое приложение сразу после показа (в пределах 1,2 с) окно не прокручивает — только явное действие (придирка 4)
const FOCUS = `(() => {
  const a = document.activeElement; if (!a || a === document.body) return { none: true };
  const b = a.getBoundingClientRect(), cs = getComputedStyle(a);
  let seen = b.height > 0 && b.top >= -0.5 && b.bottom <= innerHeight + 0.5;
  for (let p = a.parentElement; p && seen; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if (o === 'auto' || o === 'scroll' || o === 'hidden') { const pb = p.getBoundingClientRect(); if (b.top < pb.top - 0.5 || b.bottom > pb.bottom + 0.5) seen = false; }
  }
  const scr = Math.max(0, ...[...document.querySelectorAll('*')].map((e) => e.scrollTop || 0));
  return { gk: a.getAttribute('data-gk'), seen, ring: a.matches(':focus-visible') && cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0, scr };
})()`;
const key = async (k) => {
  await cdp("Input.dispatchKeyEvent", { type: "keyDown", key: k, code: k, windowsVirtualKeyCode: 13, text: "\r" });
  await cdp("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: k, windowsVirtualKeyCode: 13 });
};
async function kbdWhole(where) {
  await js(`(() => { document.querySelector('[data-gk="qrwhole"]').focus(); return 1; })()`);
  await key("Enter"); await sleep(300);
  const focus = await js(FOCUS);
  // колесом чуть назад (код снова срезан) и сразу другое приложение: блок меняет высоту — окно не должно ехать к коду
  const at = await js(`(() => { const b = document.querySelector('.gs .gs-qr').getBoundingClientRect(); return [Math.round(b.left + b.width / 2), Math.round(b.top + 20)]; })()`);
  await cdp("Input.dispatchMouseEvent", { type: "mouseWheel", x: at[0], y: at[1], deltaX: 0, deltaY: -120 }); await sleep(200);
  const back = await js(FOCUS);
  await gk("app:amneziawg"); await sleep(450);
  const app = await js(FOCUS);
  await probe(where, { app: "amneziawg", focus, scrBack: back.scr, scrApp: app.scr });
}
await panel(1366, 657); await kbdWhole("panel-kbd-whole");
await panel(1280, 689); await kbdWhole("panel-kbd-whole");
await wizard(1280, 689); await kbdWhole("wizard-kbd-whole");
// «Выдать заново» (другая платформа): раскладка не меняется и пока идёт выдача — колонки видео нет, ширина шага та же,
// кнопка «Выдать» на своём месте до, во время (сразу после нажатия) и после (решение 3.3.2 №7; третий круг №2)
for (const [w, h] of [[1280, 720], [1440, 900], [1100, 800]]) {
  await wizard(w, h);
  await js(`(() => { document.querySelector('[data-k="pf-android"]').click(); return 1; })()`); await settle();
  await until(`!!document.querySelector('[data-k="issue"]')`);
  await js(`(() => { document.querySelector('[data-k="issue"]').scrollIntoView({ block: 'center', behavior: 'instant' }); return 1; })()`); await sleep(200);
  const BT = `(() => { const b = document.querySelector('[data-k="issue"]'); const m = document.getElementById('qs-main'); return { video: !!document.querySelector('.qs-video'), mainW: m ? Math.round(m.getBoundingClientRect().width) : null, top: b ? Math.round(b.getBoundingClientRect().top) : null }; })()`;
  // ответ сервера — через 1,2 с (как настоящий: person + issue), замер «во время» — после отрисовки, до ответа
  await js(`(() => { const M = window.KASKAD_SETUP_MOCK, f = M.fetch; M.fetch = (u, i) => /step\\/(person|issue)/.test(String(u)) ? new Promise((ok) => setTimeout(ok, 1200)).then(() => f(u, i)) : f(u, i); return 1; })()`);
  const before = await js(BT);
  await js(`(() => { document.querySelector('[data-k="issue"]').click(); return 1; })()`);
  await sleep(400);
  // идёт выдача: прежнее окно приглушено (.qs-card[data-held]) или окна ещё нет
  const during = await js(`(() => Object.assign(${BT}, { busy: !!document.querySelector('.qs-card[data-held]') || !document.querySelector('.qs-give .gs .gs-step') }))()`);
  await until(`!document.querySelector('.qs-card[data-held]') && !!document.querySelector('.qs-give .gs .gs-step')`, 20000); await settle();
  const after = await js(BT);
  await probe("wizard-reissue", { app: "defaultvpn", before, during, after });
}
for (const w of [390, 360]) {
  await wizard(w, 844); await gk("qrph"); await settle(); await probe("wizard-phone", { app: "defaultvpn" });
  await gk("denseshow"); await settle(); await probe("wizard-phone-anyway", { app: "defaultvpn" });
}
console.log(JSON.stringify(res));
ws.close(); stop(); process.exit(0);
