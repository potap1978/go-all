// k3_stream_check.mjs — страж «Режима стрима» (handoff/briefs/PLAN-stream-mode-2026-10-29.md §4). Настоящий Chrome на
// моке (?mock=1): при включённом режиме в тексте и атрибутах страниц и окон нет ни одного чувствительного значения мока,
// а при выключенном они есть (иначе проверка ничего не доказывает).
//
// Секреты мока S — из двух независимых источников, чтобы ошибка в реестре продукта (stream.js) не прятала утечку:
//   1. литералы мока: адрес сервера (параметр host=, не совпадает с образцом 203.0.113.5 в поле), домены сайта, VLESS,
//      подписки и выходов, адреса выходов, отпечаток панели, pin Hysteria2;
//   2. ответы мока, пойманные в странице: логины и адреса устройств, /p/ и подписки, имя и чат бота, почта, адрес и
//      секретный путь панели, отпечатки; плюс location.host.
// Образцы: ключи vless:// vpn:// … (не меньше 8 знаков после «://»), /p/<токен>, login?t=<токен>.
//
// Что собирается: document.title, body.innerText; атрибуты title, aria-label, aria-description, alt, href, value
// (кроме <option>), data-tip у всех узлов (и SVG); placeholder — у пустых полей; .value полей без type=password и без
// -webkit-text-security. В отчёте — место, атрибут и номер секрета в S, но НЕ само значение.
//
// Проверки (строки JSON в stdout, последняя — итог):
//   leaks   — все разделы и вкладки описи SECTIONS и окна (выдача AWG/VLESS/L2TP и подписка, «Добавить ключ» со
//             вставленным vless://, адрес подписки, настройки open=host|bot|mcp|password|link|export|import, поиск
//             «198.51», инспекторы схемы), 1440 и 390, RU и EN;
//   qr      — в окне выдачи кода нет в DOM до «Показать QR», после — есть; «Скрыть QR» — снова нет;
//   reveal  — глаз показывает значение, через _ttl оно пропадает, второй щелчок прячет сразу;
//   copy    — «Скопировать» у скрытого адреса кладёт в буфер настоящее значение, на экране — маска;
//   (в leaks ещё: qrs — нарисованных QR в месте; overlap — шторка QR перекрывает кнопки окна выдачи, должно быть 0)
//   field   — поле-секрет «Шлюза»: точки стилем (не type=password), глаз показывает, второй щелчок прячет;
//   страница не отвечает 45 с (вечный круг наблюдателя) — выход 4 с местом; совпадения с S — по границе слова.
//   reverse — те же места при выключенном режиме: из S найдено не меньше K значений, наблюдателя нет, масок нет.
// Правки REV-stream (каждая падает на 827edf5d):
//   password — новый пароль панели точками после «Придумать» и в окне «Доступ из интернета» без пароля (pw=none);
//              выключен — виден (обратная);
//   offFocus — окно «Выключить режим стрима?»: фокус на «Оставить включённым», Enter режим не выключает;
//   names    — «И имена»: каждый раздел напрямую (1440 — все, 390 — четыре), имён из базы мока нет нигде;
//   alias    — подпись «Пользователь N» одного человека на «Схеме» и в «Пользователях» одна;
//   av       — (Н-4 стенда 3.3.7.13) в names у каждого места и в окнах avWin («График активности» на 1440, лист
//              человека и «Схема целиком» на 390; search — поиск в «Пользователях» по началу имени): кружки людей (.kk-av, .kg-nd__ava) — не инициалы и не оттенок настоящего имени мока
//              (свой расчёт по базе мока, не продукта), текст кружка — вид замены «П1» или «?»;
//   exitAddr — «Адрес» в раскрытии строки выхода: глаз, показ с отсчётом;
//   botToken — поле токена бота после перерисовки остаётся точками.
// Слияние в 3.3.7.13 (заход 2б):
//   mipRefusal — «Адреса сервера»: отказ «адреса у сервера нет» в «Шлюзе» (мок mip=1) — адреса сервера под маской,
//                выключен — второй адрес виден.
// node tools/k3_stream_check.mjs <адрес статики панели> <chrome> [--quick]  → JSON-строки в stdout
// K3_STREAM_SHOTS=<папка> — снимки каждого места (для сверки с макетом)
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2], CHROME = process.argv[3], QUICK = process.argv.includes("--quick");
const SHOTS = process.env.K3_STREAM_SHOTS || "";
if (SHOTS) mkdirSync(SHOTS, { recursive: true });
const SRV = "203.0.113.77";
const MOCKQ = `mock=1&host=${SRV}&site=le&vless=tls&exits=5&bot=ok&mcp=ok&public=1`;
const prof = mkdtempSync(join(tmpdir(), "k3stream-"));
const chrome = spawn(CHROME, ["--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + prof, "--no-first-run",
  "--hide-scrollbars", "--window-size=1440,1000", "about:blank"], { stdio: "ignore", detached: true });
const stop = () => { try { process.kill(-chrome.pid, "SIGKILL"); } catch (e) { try { chrome.kill("SIGKILL"); } catch (x) { /* уже нет */ } } try { rmSync(prof, { recursive: true, force: true }); } catch (e) { /* нет */ } };
process.on("exit", stop);
setTimeout(() => { console.error("k3_stream_check: время вышло"); process.exit(3); }, 1500000).unref();   // ~230 мест (с правками REV-stream) — с запасом на загруженную машину
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
let seq = 0; const wait = new Map(); const events = [];
ws.addEventListener("message", (ev) => { const m = JSON.parse(ev.data); if (m.id && wait.has(m.id)) { wait.get(m.id)(m); wait.delete(m.id); } else if (m.method) events.push(m); });
// страница не отвечает (вечный круг наблюдателя и т. п.) — сразу ошибка с местом, а не общий срок через 25 минут
let CUR = "запуск";
const cdp = (method, params = {}) => new Promise((ok, bad) => { const id = ++seq; const dead = setTimeout(() => { console.error(`k3_stream_check: страница не отвечает 45 с (${method}) в «${CUR}» — зацикливание?`); process.exit(4); }, 45000);
  wait.set(id, (m) => { clearTimeout(dead); return m.error ? bad(new Error(method + ": " + m.error.message)) : ok(m.result); }); ws.send(JSON.stringify({ id, method, params })); });
const js = async (expr) => { const r = await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails) throw new Error("js: " + JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value; };
// тишина в DOM: ждём, пока счётчик изменений не стоит 350 мс подряд (часы и живые графики — не дольше max)
async function settle(max = 4000) { const end = Date.now() + max; let last = -1, since = Date.now(); while (Date.now() < end) { let n = -1; try { n = await js("window.__k3mut || 0"); } catch (e) { /* грузится */ } if (n !== last) { last = n; since = Date.now(); } else if (Date.now() - since >= 350) return true; await sleep(70); } return false; }
async function until(expr, ms = 15000) { const end = Date.now() + ms; while (Date.now() < end) { try { if (await js(expr)) return true; } catch (e) { /* грузится */ } await sleep(120); } return false; }
await cdp("Page.enable"); await cdp("Runtime.enable");

// До загрузки: флаг режима по параметру k3stream (1 — включён, 0 — нет ключа), ловушка буфера обмена, ответы мока
await cdp("Page.addScriptToEvaluateOnNewDocument", { source: `(() => {
  const q = new URLSearchParams(location.search);
  try { if (q.get("k3stream") === "1") localStorage.setItem("kaskad.stream", "1"); else localStorage.removeItem("kaskad.stream");
    if (q.get("k3names") === "1") localStorage.setItem("kaskad.stream.names", "1"); else localStorage.removeItem("kaskad.stream.names"); } catch (e) {}
  window.__k3clip = []; window.__k3bodies = []; window.__k3mut = 0;
  try { new MutationObserver(() => { window.__k3mut++; }).observe(document, { subtree: true, childList: true, attributes: true, characterData: true }); } catch (e) {}
  try { Object.defineProperty(Navigator.prototype, "clipboard", { configurable: true, get() { return { writeText: async (s) => { window.__k3clip.push(String(s)); } }; } }); } catch (e) {}
  const wrap = (f) => function (url, init) {
    return Promise.resolve(f.call(this, url, init)).then((r) => { try { r.clone().text().then((t) => { if (t && t.length < 400000) window.__k3bodies.push(t); }, () => {}); } catch (e) {} return r; });
  };
  let real;
  Object.defineProperty(window, "KASKAD_MOCK", { configurable: true, enumerable: true, get() { return real; },
    set(x) { real = x && typeof x === "object" ? new Proxy(x, { get(t, k) { const v = t[k]; return k === "fetch" && typeof v === "function" ? wrap(v) : v; } }) : x; } });
})();` });

// ── Секреты мока ──
const MIP_NAT = "198.51.100.61";                  // второй адрес сервера в моке (mip=1, «Адреса сервера»)
const LIT = [SRV, MIP_NAT, "vpn.example.com", "vpn.example.net", "sub.example.net", "vienna.example.net", "tokyo.example.net", "hel.example.net",
  "new.example.net", "home.example.com", "dacha.example.com", "office.example.com", "198.51.100.23",
  "AB:12:CD:34:EF:56:78:90", "3f1c5a0e9b7d2c4f6a8e1b3d5c7f9a0b"];
const PRIV = /^(10\.|127\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.|169\.254\.)/;
// значения из ответов: логины и адреса устройств, /p/ и подписки, бот, почта, отпечатки, адрес и путь панели
function fromBodies(bodies, here) {
  const out = new Set([here]);
  const take = (k, v) => {
    if (typeof v !== "string" && typeof v !== "number") return;
    const s = String(v).trim();
    if (s.length < 4 || PRIV.test(s) || /^\d{1,5}$/.test(s)) return;
    if (["login", "username", "chat_id", "email", "fingerprint", "pin"].includes(k)) out.add(s);
    else if (k === "addr" && !/^https?:/.test(s)) out.add(s.replace(/:\d+$/, ""));
    else if (["url", "full", "page_url", "sub_url"].includes(k) && /^https?:\/\//.test(s)) {
      const m = /^https?:\/\/([^/:]+)(?::\d+)?(\/[^?#]*)?/.exec(s);
      // голые example.com/net/org (RFC 2606) — образцы мока для ссылок хостингов и шаблонов, а не адрес узла
      if (m && !PRIV.test(m[1]) && !/^example\.(?:com|net|org)$/i.test(m[1]) && !/apple\.com|google\.com|github\.com|amnezia|microsoft|mozilla|f-droid|apps\./.test(m[1])) {
        out.add(m[1]);
        const path = (m[2] || "").replace(/^\/|\/$/g, "");
        if (path.length >= 8 && !path.includes("•")) out.add(path.split("/").pop().length >= 8 ? path.split("/").pop() : path);
      }
    }
  };
  const walk = (j, d) => { if (!j || typeof j !== "object" || d > 9) return; if (Array.isArray(j)) { j.forEach((x) => walk(x, d + 1)); return; } for (const k of Object.keys(j)) { if (j[k] && typeof j[k] === "object") walk(j[k], d + 1); else take(k, j[k]); } };
  for (const b of bodies) { try { walk(JSON.parse(b), 0); } catch (e) { /* не JSON */ } }
  return [...out].filter((x) => x && x.length >= 4);
}
const S = new Set(LIT);

// Сканер в странице: что видно на экране (текст, атрибуты, значения полей). Значение в отчёт не попадает — только № в S
const SCAN = `(S) => {
  const low = S.map((s) => s.toLowerCase()), hits = [];
  const pats = [/\\b(?:vless|vmess|trojan|ss|hy2|hysteria2|tuic|vpn|wireguard|awg)\\:\\/\\/[A-Za-z0-9][^\\s"'<>]{7,}/i, /\\/p\\/[A-Za-z0-9_-]{8,}/, /login\\?t=[A-Za-z0-9_-]{8,}/];
  const where = (e) => (e.tagName || "?").toLowerCase() + (e.id ? "#" + e.id : "") + (typeof e.className === "string" && e.className ? "." + e.className.trim().split(/\\s+/).slice(0, 2).join(".") : "");
  const check = (text, at, attr) => {
    if (!text) return;
    const l = String(text).toLowerCase();
    // по границам слова, как сетка: логин «admin» в «administrator» — не утечка
    const W = /[\\p{L}\\p{N}_]/u;
    const hit = (s) => { for (let k = l.indexOf(s); k >= 0; k = l.indexOf(s, k + 1)) { if (!W.test(l.charAt(k - 1)) && !W.test(l.charAt(k + s.length))) return true; } return false; };
    low.forEach((s, i) => { if (hit(s)) hits.push({ at, attr, s: i }); });
    pats.forEach((p, i) => { if (p.test(text)) hits.push({ at, attr, p: i }); });
  };
  check(document.title, "title", "text");
  check(document.body.innerText, "body", "text");
  for (const e of document.querySelectorAll("*")) {
    for (const a of ["title", "aria-label", "aria-description", "alt", "href", "value", "data-tip"]) { if (!(e.tagName === "OPTION" && a === "value") && e.hasAttribute(a)) check(e.getAttribute(a), where(e), a); }
    const field = e.tagName === "INPUT" || e.tagName === "TEXTAREA";
    if (e.hasAttribute("placeholder") && (!field || !e.value)) check(e.getAttribute("placeholder"), where(e), "placeholder");
    if (field && e.type !== "password" && e.type !== "hidden" && getComputedStyle(e).webkitTextSecurity !== "disc") check(e.value, where(e), ".value");
  }
  return hits;
}`;
const IDLE = `(() => { const D = window.KASKAD_DEBUG; return !!D && Object.keys(D.R).every((k) => !D.R[k].run); })()`;
const READY = `(() => { const p = document.querySelector('.gx-page:not([hidden])'); return !!p && p.children.length > 0 && !document.querySelector('.gx-skel'); })()`;
let shotN = 0;
async function shot(name) {
  if (!SHOTS) return;
  const r = await cdp("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(SHOTS, String(++shotN).padStart(3, "0") + "-" + name.replace(/[^a-z0-9.=-]+/gi, "_") + ".png"), Buffer.from(r.data, "base64"));
}
// q — запрос мока (по умолчанию MOCKQ), names — галочка «И имена» до первой загрузки
async function open(w, on, lang, hash, q = MOCKQ, names = false) {
  const phone = w < 700;
  await cdp("Emulation.setDeviceMetricsOverride", { width: w, height: phone ? 844 : 1000, deviceScaleFactor: 1, mobile: phone });
  await cdp("Page.navigate", { url: "about:blank" }); await sleep(80);
  events.length = 0;
  await cdp("Page.navigate", { url: `${BASE}/index.html?${q}&lang=${lang}&k3stream=${on ? 1 : 0}${names ? "&k3names=1" : ""}#${hash}` });
  const ok = await until(READY, 20000);
  // ответы раздела пришли (реестр сетки знает их значения): на загруженной машине тишина DOM наступает раньше —
  // и пример «vpn.example.com» в предпросмотре «Вида», совпадающий с доменом мока, считался бы утечкой
  await until(IDLE, 10000);
  await settle();
  return ok;
}
const errors = () => events.filter((e) => e.method === "Runtime.exceptionThrown" || (e.method === "Runtime.consoleAPICalled" && e.params.type === "error"))
  .map((e) => e.method === "Runtime.exceptionThrown" ? "exception: " + ((e.params.exceptionDetails.exception || {}).description || e.params.exceptionDetails.text).slice(0, 200)
    : "console.error: " + e.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 200));
async function collect() {
  const here = await js("location.host");
  fromBodies(await js("window.__k3bodies || []"), here).forEach((x) => S.add(x));
}
async function scan() { await collect(); return js(`(${SCAN})(${JSON.stringify([...S])})`); }
const out = [];
const emit = (row) => { out.push(row); console.log(JSON.stringify(row)); };
const clickText = (re, scope) => js(`(() => { const b = [...document.querySelectorAll(${JSON.stringify(scope || "button, [role=tab], [role=menuitem], a")})].find((x) => ${re}.test(x.textContent.trim()) && !x.disabled); if (!b) return false; b.click(); return true; })()`);
const clickSel = (sel) => js(`(() => { const b = document.querySelector(${JSON.stringify(sel)}); if (!b) return false; b.click(); return true; })()`);
const setVal = (sel, v) => js(`(() => { const f = document.querySelector(${JSON.stringify(sel)}); if (!f) return false; f.focus(); f.value = ${JSON.stringify(v)}; f.dispatchEvent(new Event("input", { bubbles: true })); return true; })()`);

// ── Места ──
const ROUTES = ["overview", "overview?tab=now", "overview?tab=history", "forwards", "inbounds", "people", "exits?tab=keys", "exits?tab=default", "exits?tab=list",
  "exits?tab=groups", "exits?tab=ru", "rules?tab=profile", "rules?tab=block", "rules?tab=lists", "rules?tab=trace", "scheme",
  ...["look", "access", "gateway", "bot", "notify", "site", "github", "backup", "about", "time", "storage", "health"].map((x) => "settings?tab=" + x)];
const OPENS = ["settings?open=host", "settings?open=bot", "settings?open=mcp", "settings?open=password", "settings?open=link", "settings?open=export", "settings?open=import"];
const ISSUE_DEV = "d-7ae67ae0", L2TP_DEV = "d-f5a6b7c8";
// окно выдачи: компьютер — значок протокола в строке устройства; телефон — человек → устройство → «Подключить»
async function openIssue(w, dev, proto) {
  if (w >= 700) {
    return js(`(() => { const r = document.querySelector('.gx-xt__r[data-did="${dev}"]'); if (!r) return false; const g = r.closest('.gx-xt__g'); if (g && !g.classList.contains('is-open')) { const x = g.querySelector('.gx-xt__x'); if (x) x.click(); }
      const b = [...r.querySelectorAll('button')].find((x) => ${proto}.test(x.getAttribute('aria-label') || '')); if (!b) return false; b.click(); return true; })()`);
  }
  const ok = await js(`(() => { const r = [...document.querySelectorAll('.kk-plist .kk-prow')].find((x) => /Мама|Mom|Папа|Dad/.test(x.textContent) && x.textContent.indexOf(${JSON.stringify(dev === ISSUE_DEV ? "Мам" : "Пап")}) >= 0 || (${JSON.stringify(dev === ISSUE_DEV ? "Mom" : "Dad")} && x.textContent.indexOf(${JSON.stringify(dev === ISSUE_DEV ? "Mom" : "Dad")}) >= 0)); if (!r) return false; r.querySelector('.kk-prow__main').click(); return true; })()`);
  if (!ok) return false;
  await until(`!!document.querySelector(".gx-bsheet--act .gx-mrow[data-hold-row='dev:${dev}']")`, 5000);
  if (!(await js(`(() => { const r = document.querySelector(".gx-bsheet--act .gx-mrow[data-hold-row='dev:${dev}']"); if (!r) return false; r.click(); return true; })()`))) return false;
  await until(`!!document.querySelector('.gx-bsheet--act .gx-mrow--nav')`, 5000);
  return js(`(() => { const b = document.querySelector('.gx-bsheet--act .gx-mrow--nav'); if (!b) return false; b.click(); return true; })()`);
}
const OVERLAP = `(() => { const c = document.querySelector('.ks-qrc'); if (!c) return -1; const a = c.getBoundingClientRect(); let n = 0;
  for (const b of document.querySelectorAll('.gx-dlg button, .gx-dlg .ks-key, .gx-sheet button, .gx-bsheet button')) { if (c.contains(b)) continue; const r = b.getBoundingClientRect();
    if (r.width && r.height && r.left < a.right - 1 && r.right > a.left + 1 && r.top < a.bottom - 1 && r.bottom > a.top + 1) n++; } return n; })()`;
const OPENED = `!!document.querySelector('.gx-dlg, .gx-sheet, .gx-bsheet')`;

async function place(w, on, lang, name, fn) {
  CUR = `${on ? "вкл" : "выкл"} ${w} ${lang} ${name}`;
  const steps = [];
  let ok = true;
  try { ok = await fn(steps); } catch (e) { ok = false; steps.push("error: " + String(e.message || e).slice(0, 160)); }
  await settle();
  const hits = await scan();
  const shown = await js("document.querySelectorAll('.ks-mask, [data-stream]').length");
  const active = await js("!!(window.KaskadStream && window.KaskadStream.active)");
  const qrs = await js("document.querySelectorAll('.gx-qrart__ink').length");          // нарисованные QR (qr.js)
  await shot(`${on ? "on" : "off"}-${w}-${lang}-${name}`);
  emit({ kind: on ? "leaks" : "reverse", where: name, w, lang, ok: !!ok, steps, hits, masks: shown, active, qrs, errors: errors() });
}

async function walkAll(w, on, lang) {
  for (const r of (QUICK ? ["overview", "people", "settings?tab=gateway", "settings?tab=access"] : ROUTES)) await place(w, on, lang, r, async () => open(w, on, lang, r));
  for (const r of (QUICK ? [] : OPENS)) await place(w, on, lang, r, async (st) => { const ok = await open(w, on, lang, r); st.push("dialog:" + (await until(OPENED, 5000))); return ok; });
  // «Добавить ключ»: вставленный ключ vless:// в поле; адрес подписки
  await place(w, on, lang, "exits?open=add+paste", async (st) => {
    const ok = await open(w, on, lang, "exits?open=add"); await until(`!!document.querySelector(".gx-dlg textarea, .gx-sheet textarea, .gx-bsheet textarea")`, 5000);
    st.push("paste:" + (await setVal(".gx-dlg textarea, .gx-sheet textarea, .gx-bsheet textarea", "vless://0b3c6a1e-7f6d-4b0e-9a49-3a0f6c1d2e33@tokyo.example.net:443?security=reality&sni=www.microsoft.com#Tokyo")));
    return ok;
  });
  await place(w, on, lang, "exits?open=sub+url", async (st) => {
    const ok = await open(w, on, lang, "exits?open=sub"); await until(OPENED, 5000);
    st.push("url:" + (await setVal(".gx-dlg input[type=url], .gx-dlg input.gx-input, .gx-sheet input.gx-input, .gx-bsheet input.gx-input", "https://sub.example.net/s/9f8e7d6c5b4a")));
    return ok;
  });
  // окна выдачи: AWG 3.1, VLESS, L2TP (поля), подписка
  for (const [nm, dev, proto] of [["issue-awg", ISSUE_DEV, "/AmneziaWG 3\\.1/"], ["issue-vless", ISSUE_DEV, "/VLESS/"], ["issue-l2tp", L2TP_DEV, "/L2TP/"]]) {
    if (QUICK && nm !== "issue-awg") continue;
    if (w < 700 && nm !== "issue-awg") continue;
    await place(w, on, lang, nm, async (st) => {
      const ok = await open(w, on, lang, "people");
      st.push("open:" + (await openIssue(w, dev, proto)));
      await until(`!!document.querySelector('.gs-key, .gs-fields, .gs-host .gs-cover, .ks-qrc')`, 8000);
      await settle();
      st.push("static:" + (await clickSel('[data-qr-pick="static"]')));
      // шторка QR стоит вместо кода, а не слоем поверх кнопок окна: пересечений с кнопками и полями нет
      if (on) { await until(`!!document.querySelector('.ks-qrc')`, 6000); await settle(); st.push("overlap:" + (await js(OVERLAP))); }
      return ok;
    });
  }
  if (!QUICK && w >= 700) {
    await place(w, on, lang, "issue-sub", async (st) => {
      const ok = await open(w, on, lang, "people");
      st.push("open:" + (await openIssue(w, ISSUE_DEV, "/AmneziaWG 3\\.1/")));
      await until(`!!document.querySelector('[data-qr-pick="sub"]')`, 8000);
      st.push("sub:" + (await clickSel('[data-qr-pick="sub"]')));
      await settle();
      st.push("qr:" + (await clickText("/^(QR-код|QR code)$/")));
      return ok;
    });
    // поиск по адресу выхода
    await place(w, on, lang, "find-198.51", async (st) => {
      const ok = await open(w, on, lang, "overview");
      st.push("find:" + (await js(`(() => { const b = document.querySelector('.gx-find-btn, [data-find-open], .gx-search-in, button[aria-keyshortcuts]'); if (b) b.click(); return !!b; })()`)));
      await until(`!!document.querySelector(".gx-find input, [role=dialog] input[type=search], [role=dialog] input.gx-input")`, 5000);
      st.push("type:" + (await setVal(".gx-find input, [role=dialog] input[type=search], [role=dialog] input.gx-input", "198.51")));
      return ok;
    });
    // инспекторы схемы: устройство, выход, сервер
    await place(w, on, lang, "scheme-inspect", async (st) => {
      const ok = await open(w, on, lang, "scheme"); await until(`!!document.querySelector('[data-kind="device"]')`, 8000);
      for (const sel of ['[data-kind="device"]', '[data-kind="exit"]', '[data-kind="gateway"], [data-kind="server"]']) {
        st.push(sel + ":" + (await js(`(() => { const n = document.querySelector(${JSON.stringify(sel)}); if (!n) return false; n.dispatchEvent(new MouseEvent("click", { bubbles: true })); return true; })()`)));
        await settle();
        const h = await scan(); if (h.length) st.push("hits@" + sel + ":" + h.length);
      }
      return ok;
    });
  }
}

for (const lang of (QUICK ? ["ru"] : ["ru", "en"])) for (const w of [1440, 390]) await walkAll(w, true, lang);

// ── QR, показ, копирование (включён) ──
{
  const w = 1440, lang = "ru", steps = [];
  await open(w, true, lang, "people");
  steps.push("open:" + (await openIssue(w, ISSUE_DEV, "/AmneziaWG 3\\.1/")));
  await until(`!!document.querySelector('.ks-qrc, .gs-qr')`, 8000);
  await settle();
  const frame = () => js(`(() => { const f = document.querySelector('.gs-qr__frame'); return f ? f.querySelectorAll('svg, canvas, img, path').length : 0; })()`);
  const before = { cover: await js("!!document.querySelector('.ks-qrc')"), frame: await frame() };
  steps.push("show:" + (await clickSel('.ks-qrc button')));
  await until(`!document.querySelector('.ks-qrc') && !!document.querySelector('.gs-qr__frame svg, .gs-qr__frame canvas, .gs-qr__frame img')`, 5000);
  const after = { cover: await js("!!document.querySelector('.ks-qrc')"), frame: await frame() };
  steps.push("hide:" + (await clickSel('.ks-qrhide')));
  await until(`!!document.querySelector('.ks-qrc')`, 5000);
  const again = { cover: await js("!!document.querySelector('.ks-qrc')"), frame: await frame() };
  await shot("qr-check");
  emit({ kind: "qr", w, lang, before, after, again, steps, errors: errors() });
}
{
  // показ: «О панели» — глаз у адреса панели (строка-секрет с «Скопировать»); время показа — 400 мс из теста
  const w = 1440, lang = "ru";
  await open(w, true, lang, "settings?tab=about");
  const EYE = ".gx-kv__row .ks-eye";
  await js("window.KaskadStream._ttl = 400, true");
  const val = `(() => { const e = document.querySelector('[data-sec-shown] .ks-sv__v'); return e ? e.textContent : ""; })()`;
  const eye = await clickSel(EYE);
  await until(`${val} !== ""`, 3000);
  const shown = await js(val);
  await shot("reveal-shown");
  await until(`${val} === ""`, 3000);                                 // срок 400 мс: значение уходит само
  const gone = await js(val);
  await js("window.KaskadStream._ttl = 10000, true");
  await clickSel(EYE); await until(`${val} !== ""`, 3000);
  const twice1 = await js(val);
  await clickSel(EYE); await until(`${val} === ""`, 3000);
  const twice2 = await js(val);
  // копирование: «Скопировать» у скрытого адреса → в буфере значение, на экране маска
  await js("window.__k3clip.length = 0, true");
  const copied = await js(`(() => { const r = document.querySelector('.gx-kv__row .ks-eye'); const b = r && r.parentNode.querySelector('.gx-kv__copy:not(.ks-eye)'); if (!b) return false; b.click(); return true; })()`);
  await until("window.__k3clip.length > 0", 3000);
  const clip = await js("window.__k3clip.slice()");
  const mask = await js("document.querySelectorAll('.ks-mask').length");
  emit({ kind: "reveal", eye, shown: shown.length > 0 && !shown.includes("•"), goneAfterTtl: gone === "", secondClickHides: twice1.length > 0 && twice2 === "", errors: errors() });
  emit({ kind: "copy", clicked: copied, clipReal: clip.length === 1 && clip[0].length >= 4 && !clip[0].includes("•"), clipInS: clip.length === 1 && [...S].some((s) => clip[0].toLowerCase().includes(s.toLowerCase())), masksOnScreen: mask });
}
{
  // поле-секрет «Шлюза»: точки (не type=password — менеджер паролей не трогает), глаз показывает, второй щелчок прячет
  const w = 1440, lang = "ru";
  await open(w, true, lang, "settings?tab=gateway");
  const IN = "input.gx-input--mono";
  const st = (sel) => js(`(() => { const f = document.querySelector(${JSON.stringify(sel)}); return f ? { type: f.type, dots: getComputedStyle(f).webkitTextSecurity === "disc", value: f.value.length > 0 } : null; })()`);
  const before = await st(IN);
  const eye = await js(`(() => { const f = document.querySelector(${JSON.stringify(IN)}); const b = f && f.parentNode.querySelector(".ks-eye"); if (!b) return false; b.click(); return true; })()`);
  await until(`(() => { const f = document.querySelector(${JSON.stringify(IN)}); return !!f && getComputedStyle(f).webkitTextSecurity !== "disc"; })()`, 3000);
  const open1 = await st(IN);
  await js(`document.querySelector(${JSON.stringify(IN)}).parentNode.querySelector(".ks-eye").click(), true`);
  await until(`getComputedStyle(document.querySelector(${JSON.stringify(IN)})).webkitTextSecurity === "disc"`, 3000);
  const closed = await st(IN);
  emit({ kind: "field", before, eye, open: open1, closed, errors: errors() });
}

// ── Правки по REV-stream: каждый шаг падает на 827edf5d ──
const hitsOf = async (list) => (list.length ? (await js(`(${SCAN})(${JSON.stringify(list)})`)).filter((h) => h.s !== undefined) : []);
{
  // Б-1: новый пароль панели — точками и после «Придумать», и в окне «Доступ из интернета» без пароля (там он придуман
  // сразу); значения нет ни в тексте, ни в атрибутах, ни в открытом поле. Выключен — после «Придумать» он виден (обратная)
  const PWF = `(() => { const b = [...document.querySelectorAll('.gx-dlg button')].find((x) => /^(Придумать|Generate)$/.test(x.textContent.trim())); return b ? b.closest('.gx-field').querySelector('input') : null; })()`;
  const pw = async () => {
    const st = await js(`(() => { const f = ${PWF}; return f ? { type: f.type, dots: getComputedStyle(f).webkitTextSecurity === "disc", len: f.value.length, eyes: f.closest('.gx-field').querySelectorAll('.ks-eye').length } : null; })()`);
    const v = await js(`(() => { const f = ${PWF}; return f ? f.value : ""; })()`);
    return { st, hits: v ? (await hitsOf([v])).length : -1 };
  };
  for (const on of [true, false]) {
    CUR = `${on ? "вкл" : "выкл"} пароль`;
    await open(1440, on, "ru", "settings?open=password");
    await until(`!!(${PWF})`, 5000);
    const gen = await clickText("/^(Придумать|Generate)$/", ".gx-dlg button");
    await settle();
    const afterGen = await pw();
    // «Доступ из интернета» на узле без пароля (pw=none, панель не открыта)
    await open(1440, on, "ru", "settings?tab=access", `mock=1&host=${SRV}&pw=none`);
    await until(`!!document.querySelector('[data-find="settings.public"]')`, 8000);
    const sw = await clickSel('[data-find="settings.public"]');
    await until(`!!(${PWF})`, 5000);
    await settle();
    const publicOn = await pw();
    emit({ kind: "password", on, gen, sw, afterGen, publicOn, errors: errors() });
  }
}
{
  // С-2: окно «Выключить режим стрима?» — фокус на «Оставить включённым» (макет В2); Enter режим не выключает
  CUR = "вкл фокус окна выключения";
  await open(1440, true, "ru", "overview");
  const opened = await clickSel("#streamToggle");
  await until(`!!document.querySelector('.ks-dlg')`, 3000);
  const KEEP = `(document.activeElement && document.activeElement.textContent || "").trim() === "Оставить включённым"`;
  await until(KEEP, 1500);
  const focus = await js(`(document.activeElement && document.activeElement.closest('.ks-dlg') ? document.activeElement.textContent : "").trim()`);
  for (const type of ["keyDown", "keyUp"]) await cdp("Input.dispatchKeyEvent", { type, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, text: type === "keyDown" ? "\r" : undefined });
  await until(`!document.querySelector('.ks-dlg')`, 3000);
  await settle();
  emit({ kind: "offFocus", opened, focus, closed: await js(`!document.querySelector('.ks-dlg')`), on: await js("!!(window.KaskadStream && window.KaskadStream.on)"),
    attr: await js(`document.documentElement.getAttribute("data-stream")`), errors: errors() });
}
{
  // Б-2: «И имена» — каждый раздел открывается напрямую (новая загрузка): имён пользователей и устройств мока нет ни в
  // тексте, ни в атрибутах. Имена — из базы мока (KASKAD_MOCK.db()), не из реестра продукта
  const NAMES = `(() => { const d = window.KASKAD_MOCK && window.KASKAD_MOCK.db(); return d ? [...d.users.map((u) => u.name), ...d.devices.map((x) => x.name)].filter((s) => s && String(s).length >= 2) : []; })()`;
  const places = [...(QUICK ? ["overview", "scheme", "forwards", "people"] : ROUTES).map((r) => [1440, r]), ...["overview", "people", "scheme", "exits?tab=list"].map((r) => [390, r])];
  // Н-4 (стенд 3.3.7.13): производные имени — инициалы и цвет кружка человека. Две буквы поиск по S не ловит, цвет —
  // стиль: инициалы и оттенок настоящих имён мока считаются здесь (как считала панель до правки) и сверяются с каждым
  // кружком; текст кружка с «И имена» — только вид замены («П1», «U1») или «?» (имя, которого реестр не знает)
  const UNAMES = `(() => { const d = window.KASKAD_MOCK && window.KASKAD_MOCK.db(); return d ? d.users.map((u) => u.name).filter((s) => s && String(s).length >= 2) : []; })()`;
  const AVA = `((names, scope) => {
    const ini = (s) => { const w = String(s || "?").trim().split(/\\s+/).filter(Boolean); return ((w.length > 1 ? w[0][0] + w[1][0] : String(s || "?").trim().slice(0, 2)) || "?").toUpperCase(); };
    const hue = (s) => { let h = 0; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
    const I = new Set(names.map(ini)), H = new Set(names.map(hue)), hits = [];
    const av = [...document.querySelectorAll((scope ? scope + " " : "") + ".kk-av, " + (scope ? scope + " " : "") + ".kg-nd__ava")];
    av.forEach((a, i) => {
      const x = a.textContent.trim(), cls = String(a.className).split(" ")[0], m = /\\s(\\d+(?:\\.\\d+)?)\\)\\s*$/.exec(a.style.background || "");
      if (I.has(x) || !/^(?:[ПU]\\d|\\?)$/.test(x)) hits.push({ i, cls, what: "text" });
      if (m && H.has(Number(m[1]))) hits.push({ i, cls, what: "hue" });
    });
    return { n: av.length, hits };
  })`;
  const avOf = async (scope) => js(`${AVA}(${UNAMES}, ${JSON.stringify(scope || "")})`);
  for (const [w, r] of places) {
    CUR = `вкл И имена ${w} ${r}`;
    const ok = await open(w, true, "ru", r, MOCKQ, true);
    const names = await js(NAMES);
    emit({ kind: "names", where: r, w, ok: !!ok, n: names.length, hits: await hitsOf(names), av: await avOf(), errors: errors() });
  }
  // окна с кружком человека: «График активности» (1440, «Ещё действия» строки) и лист человека (390, нажатие на строку)
  const UID0 = `(window.KASKAD_MOCK.db().users[0] || {}).id`;
  CUR = "вкл И имена окно активности";
  await open(1440, true, "ru", "people", MOCKQ, true);
  const actOpen = await js(`(async () => { const r = document.querySelector('[data-uid="' + ${UID0} + '"]'); const b = r && [...r.querySelectorAll('button[aria-haspopup="menu"]')].find((x) => /^(Ещё действия|More actions)/.test(x.getAttribute('aria-label') || '')); if (!b) return false; b.click(); await new Promise((ok) => setTimeout(ok, 250));
    const m = [...document.querySelectorAll('[role=menuitem]')].find((x) => /График активности/.test(x.textContent)); if (!m) return false; m.click(); return true; })()`);
  await until(`!!document.querySelector('.gx-dlg--act .kk-av')`, 5000);
  await settle();
  const actWin = { opened: actOpen, av: await avOf(".gx-dlg--act"), hits: await hitsOf(await js(NAMES)) };
  CUR = "вкл И имена лист человека 390";
  await open(390, true, "ru", "people", MOCKQ, true);
  const shOpen = await js(`(() => { const b = document.querySelector('.kk-prow[data-uid="' + ${UID0} + '"] .kk-prow__main'); if (!b) return false; b.click(); return true; })()`);
  await until(`!!document.querySelector('.gx-bsheet .kk-av')`, 5000);
  await settle();
  const sheet = { opened: shOpen, av: await avOf(".gx-bsheet"), hits: await hitsOf(await js(NAMES)) };
  // телефон: «Схема» — список устройств без кружков, кружки людей — в «Схеме целиком»
  CUR = "вкл И имена схема целиком 390";
  await open(390, true, "ru", "scheme", MOCKQ, true);
  const fullOpen = await clickText("/^(Схема целиком|The whole scheme)/", "button, a, [role=button]");
  await until(`!!document.querySelector('.kg-nd__ava')`, 8000);
  await settle();
  const full = { opened: fullOpen, av: await avOf(), hits: await hitsOf(await js(NAMES)) };
  // поиск в «Пользователях» по началу настоящего имени: подсветка совпадения не режет имя на части мимо сетки
  // («Мам» + «а» — каждая часть короче имени, сетка её не узнаёт)
  CUR = "вкл И имена поиск";
  await open(1440, true, "ru", "people", MOCKQ, true);
  const q3 = await js(`String((window.KASKAD_MOCK.db().users[0] || {}).name || "").slice(0, 3).toLowerCase()`);
  const typed = await setVal('input[data-keep="ppq"]', q3);
  await settle();
  const search = { typed, q: q3.length, rows: await js(`document.querySelectorAll('section[data-page="people"] .gx-xt__r').length`), hits: await hitsOf(await js(NAMES)) };
  emit({ kind: "avWin", act: actWin, sheet, full, search, errors: errors() });
  // подпись одного и того же человека на «Схеме» и в «Пользователях» одна («Пользователь N» по порядку добавления)
  const UID = `(window.KASKAD_MOCK.db().users[0] || {}).id`;
  // подпись — из aria-label узла и строки (в textContent цифры соседних ячеек слипаются с номером: «Пользователь 12»)
  const AL = /(?:Пользователь|User) \d+(?![\d])/.source;
  CUR = "вкл И имена подпись";
  await open(1440, true, "ru", "scheme", MOCKQ, true);
  await until(`!!document.querySelector('.kg-nd')`, 8000);
  const onScheme = await js(`(() => { const n = document.querySelector('.kg-nd[data-id*="' + ${UID} + '"]'); const m = n && new RegExp(${JSON.stringify(AL)}).exec(n.getAttribute("aria-label") || ""); return m ? m[0] : null; })()`);
  await open(1440, true, "ru", "people", MOCKQ, true);
  const onPeople = await js(`(() => { const n = document.querySelector('.gx-xt__r[data-uid="' + ${UID} + '"]'); const m = n && new RegExp(${JSON.stringify(AL)}).exec(n.getAttribute("aria-label") || ""); return m ? m[0] : null; })()`);
  emit({ kind: "alias", scheme: onScheme, people: onPeople, errors: errors() });
}
{
  // З-1: «Выходы › Все выходы», раскрытие строки — «Адрес» маской с глазом; глаз показывает адрес с отсчётом
  CUR = "вкл адрес выхода";
  await open(1440, true, "ru", "exits?tab=list");
  await js(`(() => { document.querySelectorAll('section[data-page="exits"] .gx-xt__g').forEach((g) => { if (!g.classList.contains('is-open')) { const x = g.querySelector('.gx-xt__x'); if (x) x.click(); } }); return true; })()`);
  await settle();
  const eyes = await js(`document.querySelectorAll('.kk-exd__r .ks-eye').length`);
  const clicked = await clickSel(".kk-exd__r .ks-eye");
  await until(`!!document.querySelector('.kk-exd__r [data-sec-shown] .ks-sv__v')`, 3000);
  const shown = await js(`(() => { const v = document.querySelector('.kk-exd__r [data-sec-shown] .ks-sv__v'), l = document.querySelector('.kk-exd__r [data-sec-shown] .ks-sv__left'); return { v: !!v && v.textContent.length >= 4 && !v.textContent.includes("•"), left: !!l && /\\d/.test(l.textContent) }; })()`);
  await js("window.KaskadStream.hideRevealed(), true");
  emit({ kind: "exitAddr", eyes, clicked, shown, errors: errors() });
}
{
  // З-3: «Бот» — поле токена после перерисовки (ввод текста, не похожего на токен) остаётся точками
  CUR = "вкл токен бота";
  await open(1440, true, "ru", "settings?tab=bot");
  const before = await js(`(() => { const f = document.querySelector('input.ux-in--token'); return f ? getComputedStyle(f).webkitTextSecurity === "disc" : null; })()`);
  const typed = await setVal("input.ux-in--token", "12345:abc");
  await settle();
  const after = await js(`(() => { const f = document.querySelector('input.ux-in--token'); return f ? { dots: getComputedStyle(f).webkitTextSecurity === "disc", len: f.value.length } : null; })()`);
  emit({ kind: "botToken", before, typed, after, errors: errors() });
}

{
  // 3.3.7.13 «Адреса сервера» (PLAN-multi-ip Ш2, Л3): «Шлюз» › «Адрес для клиентов» — чужой IPv4, отказ мока (mip=1)
  // перечисляет адреса сервера, второй — MIP_NAT. Включён — в тосте маски, секретов нет; выключен — второй адрес виден
  const rows = {};
  for (const [on, lang] of [[true, "ru"], [true, "en"], [false, "ru"]]) {
    CUR = `${on ? "вкл" : "выкл"} адреса сервера ${lang}`;
    await open(1440, on, lang, "settings?tab=gateway", MOCKQ + "&mip=1");
    const typed = await setVal("#stGwAddr input", "198.51.100.99");
    const saved = await clickText("/^(Сохранить|Save)$/", "#stGwAddr button");
    const toast = await until(`!!document.querySelector("#toastHost .gx-toast[role=alert]")`, 8000);
    const hits = await scan();
    const nat = await js(`document.body.innerText.includes(${JSON.stringify(MIP_NAT)})`);
    const masked = await js(`(() => { const t = document.querySelector("#toastHost .gx-toast[role=alert]"); return !!t && !!window.KaskadStream && t.textContent.includes(window.KaskadStream.MASK); })()`);
    await shot(`mip-${on ? "on" : "off"}-${lang}`);
    rows[(on ? "on-" : "off-") + lang] = { typed, saved, toast, hits, nat, masked };
  }
  emit({ kind: "mipRefusal", rows, errors: errors() });
}

// ── Обратная проверка: те же места с выключенным режимом ──
for (const w of [1440, 390]) {
  for (const r of ["settings?tab=gateway", "settings?tab=access", "settings?tab=about", "settings?tab=site", "exits?tab=keys", "people", "inbounds", "settings?tab=bot"]) {
    await place(w, false, "ru", r, async () => open(w, false, "ru", r));
  }
}
const leaks = out.filter((r) => r.kind === "leaks");
const rev = out.filter((r) => r.kind === "reverse");
const found = new Set(); rev.forEach((r) => r.hits.forEach((h) => { if (h.s !== undefined) found.add(h.s); }));
const summary = {
  kind: "summary", S: S.size, places: leaks.length,
  leakPlaces: leaks.filter((r) => r.hits.length).map((r) => r.where + "@" + r.w + "/" + r.lang),
  notOpened: leaks.filter((r) => !r.ok).map((r) => r.where + "@" + r.w + "/" + r.lang),
  errors: [...new Set(out.flatMap((r) => r.errors || []))].slice(0, 20),
  reverseFound: found.size, reverseActive: rev.some((r) => r.active), reverseMasks: rev.reduce((a, r) => a + r.masks, 0),
};
console.log(JSON.stringify(summary));
process.exit(0);
