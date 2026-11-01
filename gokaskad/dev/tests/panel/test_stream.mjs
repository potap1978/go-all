// «Режим стрима» (PLAN-stream-mode-2026-10-29 §4, «узлом»): чистые функции static/stream.js — сетка scrub, isPublicIp,
// реестр note/harvest/safe/alias. Офлайн, только node. Запуск: node tests/panel/test_stream.mjs -> «STREAM OK: N …»;
// обёртка — tests/test_stream_mode.py.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const STATIC = path.resolve(HERE, "../../lib/kaskad/panel/static");
const load = () => { const box = { console, Promise, setInterval, clearInterval }; box.globalThis = box; vm.runInNewContext(fs.readFileSync(path.join(STATIC, "stream.js"), "utf8"), box, { filename: "stream.js" }); return box.KaskadStream; };

let n = 0;
const tests = [];
const test = (name, fn) => tests.push([name, fn]);
const eq = (a, b, m) => { n++; assert.deepStrictEqual(a, b, m || "eq"); };
const ok = (v, m) => { n++; assert.ok(v, m || "ok"); };
const M = "••••••••";

test("public and private addresses", () => {
  const K = load();
  for (const ip of ["5.18.3.4", "95.217.1.2", "3.3.7.13", "2a01:4f8:c0c:1234::1", "[2606:4700::1111]"]) ok(K.isPublicIp(ip), ip);
  for (const ip of ["10.77.0.1", "172.16.0.5", "172.31.255.1", "192.168.1.1", "100.64.0.1", "100.127.1.1", "127.0.0.1", "169.254.1.1", "0.0.0.0",
    "224.0.0.1", "255.255.255.0", "192.0.2.1", "198.51.100.7", "203.0.113.5", "198.18.0.1", "2001:db8::1", "fe80::1", "fd00::1",
    "8.8.8.8", "1.1.1.1", "77.88.8.8", "9.9.9.9", "300.1.1.1", "01.2.3.4", "not an ip", ""]) ok(!K.isPublicIp(ip), ip);
});

test("patterns: keys, tokens, /p/, login links, public IPs", () => {
  const K = load();
  eq(K.scrub("ключ vless://0b3c6a1e-7f6d@vpn.example.net:443?security=tls#x — готово"), "ключ " + M + " — готово");
  eq(K.scrub("vpn://AAAAB3NzaC1yc2EAAAADAQABAAAB"), M);
  eq(K.scrub("ссылка vpn:// и vless://… в подсказке"), "ссылка vpn:// и vless://… в подсказке");     // пример без тела — не ключ
  eq(K.scrub("страница https://203.0.113.5:43125/p/AbCdEf123456 открыта"), "страница " + M + " открыта");
  eq(K.scrub("путь /p/AbCdEf123456/sub"), "путь " + M);
  eq(K.scrub("вход http://127.0.0.1:1980/login?t=Zm9vYmFyYmF6cXV4"), "вход " + M);
  eq(K.scrub("бот 1234567890:AAbbCCddEEffGGhhIIjjKKllMMnnOOppQQ"), "бот " + M);
  eq(K.scrub("ghp_0123456789abcdefABCDEF0123 и github_pat_11ABCDEFG0123456789_abcdef"), M + " и " + M);
  eq(K.scrub("PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="), "PrivateKey = " + M);
  eq(K.scrub("ключ yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk= выдан"), "ключ " + M + " выдан");
  eq(K.scrub("клиент 5.18.3.4 подключился к 95.217.1.2:443"), "клиент " + M + " подключился к " + M + ":443");
  eq(K.scrub("сервер 2a01:4f8:c0c:1234::1 ответил"), "сервер " + M + " ответил");
});

test("left alone: private, documentation, probes, times, versions, ports", () => {
  const K = load();
  for (const s of ["сеть 10.77.0.0/24 · DNS 10.77.0.1", "Точка 8.8.8.8 теряет 40%", "в 12:30:45 и 23:59", "порт 51820/udp",
    "Версия 3.3.7.13", "v3.3.7.13", "обновление 3.3.7.12 → 3.3.7.13", "3.3.0-mock", "пример 203.0.113.5 или vpn.example.com",
    "2001:db8::7", "маска 255.255.255.0", "Kaskad 3.3.7.13", "дата 2026-10-29T10:12:00Z"]) eq(K.scrub(s), s, s);
  K.safe("3.3.7.13");
  eq(K.scrub("3.3.7.13"), "3.3.7.13");                                                      // «О панели»: версия одна в ячейке
});

test("registry: hosts, URLs, logins; word edges; case; length mask", () => {
  const K = load();
  K.note("vpn.example.net", "host"); K.note("https://203.0.113.77:43125/p/AbCdEf123456", "url"); K.note("u7f3a2b9", "login");
  K.note("abc", "x"); K.note("8443", "x"); K.note("10.77.0.5", "x"); K.note("8.8.8.8", "x");               // коротко, порт, частный, точка — нет
  const k = K._known();
  ok(k.includes("vpn.example.net") && k.includes("203.0.113.77") && k.includes("u7f3a2b9"), "noted");
  ok(!k.includes("abc") && !k.includes("8443") && !k.includes("10.77.0.5") && !k.includes("8.8.8.8"), "skipped");
  eq(K.scrub("Домен VPN.Example.NET указывает на 203.0.113.77."), "Домен " + M + " указывает на " + M + ".");
  eq(K.scrub("203.0.113.77:43125 и 203.0.113.770"), M + ":43125 и 203.0.113.770");            // граница слова
  eq(K.scrub("логин u7f3a2b9, не u7f3a2b9x"), "логин " + M + ", не u7f3a2b9x");
  eq(K.scrub("пример 203.0.113.5"), "пример 203.0.113.5");
  eq(K.scrub("подпись", { known: true }), "подпись");
  eq(K.scrub("vpn.example.net").length, M.length);                                          // длина маски не выдаёт длину
});

test("harvest: keys of API answers; store links stay; own links are taken", () => {
  const K = load();
  K.harvest({ gateway: { host: "203.0.113.77" }, devices: [{ login: "dev-login-01", addr: "5.18.3.4", ips: { awg: "10.77.1.10" } }],
    site: { domain: "vpn.example.net", email: "me@example.org", a: ["203.0.113.77", "198.51.100.20"] }, panel: { fingerprint: "AB:12:CD:34", login: "admin" },
    install: { apps: [{ links: [{ url: "https://apps.apple.com/app/id1600529900" }] }] }, page: { url: "https://203.0.113.77:43125/p/QwErTy123456" },
    exits: [{ host: "tokyo.example.net", name: "Токио" }], chart: { a: 12 } });
  const k = K._known();
  for (const v of ["203.0.113.77", "dev-login-01", "5.18.3.4", "vpn.example.net", "me@example.org", "198.51.100.20", "ab:12:cd:34", "admin",
    "https://203.0.113.77:43125/p/qwerty123456", "tokyo.example.net"]) ok(k.includes(v), v);
  for (const v of ["10.77.1.10", "https://apps.apple.com/app/id1600529900", "apps.apple.com", "токио", "12"]) ok(!k.includes(v), v);
});

test("harvest: «Адреса сервера» and «IP выхода» (3.3.7.13) — known, ips, in, src take addresses only", () => {
  const K = load();
  // отказ «адреса у сервера нет» (api._known_host): адреса сервера — в data.known
  K.harvest({ ok: false, error: "…", code: 3, data: { reason: "address_not_here", ip: "198.51.100.99", known: ["203.0.113.77", "198.51.100.61", "95.217.1.2", "10.0.0.5", "8.8.8.8"] } });
  K.harvest({ ids: ["e-0a1b2c3d4e"], src: "95.217.3.4", in: ["5.18.3.4"], client: "2a01:4f8:c0c:1234::5", local: "10.0.0.5", show: true,
    devices: [{ ips: { awg: "10.77.1.10" } }], route: { src: "по умолчанию узла" }, mask: { ips: ["255.255.255.0"] } });
  const k = K._known();
  for (const v of ["198.51.100.99", "203.0.113.77", "198.51.100.61", "95.217.1.2", "95.217.3.4", "5.18.3.4", "2a01:4f8:c0c:1234::5"]) ok(k.includes(v), v);
  for (const v of ["10.0.0.5", "8.8.8.8", "10.77.1.10", "по умолчанию узла", "e-0a1b2c3d4e", "255.255.255.0", "true"]) ok(!k.includes(v), v);
  // TEST-NET стоит в моке на месте настоящих: сеткой по виду он не прячется — только реестром
  eq(K.scrub("Адреса 198.51.100.99 у этого сервера нет. Есть: 203.0.113.77, 198.51.100.61. Kaskad 3.3.7.13"),
    "Адреса " + M + " у этого сервера нет. Есть: " + M + ", " + M + ". Kaskad 3.3.7.13");
});

test("idempotent; names only with the tick; Cyrillic and punycode", () => {
  const K = load();
  K.note("vpn.пример.рф", "host"); K.note("xn--e1afmkfd.xn--p1ai", "host");
  eq(K.scrub("домен vpn.пример.рф (xn--e1afmkfd.xn--p1ai)"), "домен " + M + " (" + M + ")");
  const once = K.scrub("ключ vless://abcdefgh12345 и 5.18.3.4");
  eq(K.scrub(once), once);
  K.alias("Мама", "Пользователь 1");
  eq(K.scrub("Устройства: Мама, Мамаев"), "Устройства: Мама, Мамаев");                      // галочки «И имена» нет
  K.names = true; K.alias("Папа", "Пользователь 2");                                          // alias пересобирает выражение
  eq(K.scrub("Устройства: Мама, Мамаев, Папа"), "Устройства: Пользователь 1, Мамаев, Пользователь 2");
});

test("face: avatar initials and colour come from the alias with «И имена»; 2–3 letter names are replaced too (Н-4)", () => {
  const K = load();
  K.alias("Мама", "Пользователь 1"); K.alias("Ян", "Пользователь 2"); K.alias("Оля", "Пользователь 3");
  eq(K.face("Мама"), "Мама");                                                               // режима нет — само имя
  K.on = true;
  eq(K.face("Мама"), "Мама");                                                               // без «И имена» имя на экране
  eq(K.scrub("Ян"), "Ян");
  K.names = true;
  eq([K.face("Мама"), K.face(" мама "), K.face("Ян")], ["Пользователь 1", "Пользователь 1", "Пользователь 2"]);
  eq([K.face("Незнакомец"), K.face(""), K.face(null), K.face(undefined)], ["", "", "", ""]); // реестр не знает — «?»
  eq([K.scrub("Ян"), K.scrub("Оля"), K.scrub("Оля и Ян")], ["Пользователь 2", "Пользователь 3", "Пользователь 3 и Пользователь 2"]);
  eq([K.scrub("Янв"), K.scrub("ок"), K.scrub("Я")], ["Янв", "ок", "Я"]);                    // по границе слова; одна буква — нет
  K.forget();
  eq(K.face("Мама"), "");
});

test("flag: only «1» turns it on (old data → new: no key, garbage)", () => {
  const src = fs.readFileSync(path.join(STATIC, "stream.js"), "utf8");
  for (const [v, on] of [[null, false], ["yes", false], ["0", false], ["true", false], ["1", true]]) {
    const store = v === null ? {} : { "kaskad.stream": v };
    const attrs = {};
    const doc = { documentElement: { setAttribute: (k, x) => { attrs[k] = x; }, removeAttribute: (k) => { delete attrs[k]; } }, addEventListener() {}, removeEventListener() {},
      createTreeWalker: () => ({ nextNode: () => null }), visibilityState: "visible" };
    doc.documentElement.isConnected = true; doc.documentElement.nodeType = 1; doc.documentElement.hasAttribute = () => false; doc.documentElement.closest = () => null; doc.documentElement.tagName = "HTML";
    class MO { observe() {} disconnect() {} }
    const box = { console, Promise, setInterval, clearInterval, document: doc, MutationObserver: MO, location: { host: "203.0.113.77:43125", pathname: "/s3cr3tPathXyz/index.html" },
      localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, x) => { store[k] = x; }, removeItem: (k) => { delete store[k]; } }, addEventListener() {} };
    box.globalThis = box; box.window = box;
    vm.runInNewContext(src, box, { filename: "stream.js" });
    eq(box.KaskadStream.on, on, String(v)); eq(box.KaskadStream.active, on, "observer " + v); eq(attrs["data-stream"], on ? "on" : undefined, "attr " + v);
    ok(box.KaskadStream._known().includes("203.0.113.77:43125") && box.KaskadStream._known().includes("s3cr3tpathxyz"), "tab address noted");
  }
});

// REV-stream С-1: узел, который сетка уже правила (маска на IP), проходит её снова, когда в реестр попал домен из того же
// текста (тост или событие пришли раньше ответа API с этим доменом). Наблюдатель — настоящий код stream.js на макете
// документа из одного текстового узла
test("late registry: a node the grid already changed is checked again", async () => {
  const src = fs.readFileSync(path.join(STATIC, "stream.js"), "utf8");
  const root = { nodeType: 1, tagName: "HTML", isConnected: true, hasAttribute: () => false, closest: () => null, setAttribute() {}, removeAttribute() {} };
  const text = { nodeType: 3, data: "Выход 5.18.3.4 через late.example.org", isConnected: true, parentNode: root };
  const doc = { documentElement: root, addEventListener() {}, removeEventListener() {}, visibilityState: "visible",
    createTreeWalker: () => { let i = 0; return { nextNode: () => (i++ === 0 ? text : null) }; } };
  class MO { observe() {} disconnect() {} }
  const store = { "kaskad.stream": "1" };
  const box = { console, Promise, setInterval, clearInterval, document: doc, MutationObserver: MO, addEventListener() {},
    localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, x) => { store[k] = x; }, removeItem: (k) => { delete store[k]; } } };
  box.globalThis = box; box.window = box;
  vm.runInNewContext(src, box, { filename: "stream.js" });
  const K = box.KaskadStream;
  ok(K.active, "observer");
  eq(text.data, "Выход " + M + " через late.example.org");                                // первый проход: IP
  K.note("late.example.org", "host");
  await new Promise((r) => setTimeout(r, 0));                                                // проход по документу (микрозадача)
  eq(text.data, "Выход " + M + " через " + M);
});

for (const [name, fn] of tests) {
  try { await fn(); } catch (e) { console.error("FAIL " + name + ": " + e.message); process.exit(1); }
}
console.log("STREAM OK: " + n + " checks in " + tests.length + " tests");
