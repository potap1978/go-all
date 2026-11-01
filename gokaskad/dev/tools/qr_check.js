#!/usr/bin/env node
/* Сверка кодировщика QR панели (lib/kaskad/panel/static/qr.js) с qrencode. Запуск руками (нужны node и qrencode):

       node tools/qr_check.js [--quick]

   Для каждого сочетания «версия 1–40 × уровень L, M, Q, H» берётся строка предельной длины (она заставляет
   qrencode взять именно эту версию) и две случайные короче; матрицы сравниваются модуль в модуль. Маска qrencode
   и qr.js должна совпасть; если не совпала, но с принудительной маской qrencode матрицы одинаковы — это тоже
   видно в отчёте (спорные случаи штрафов), а ошибкой считается только расхождение при ЛЮБОЙ маске. */
"use strict";
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const src = fs.readFileSync(path.join(__dirname, "..", "lib", "kaskad", "panel", "static", "qr.js"), "utf8");
const sandbox = {};
new Function("window", "globalThis", src)(sandbox, sandbox);
const QR = sandbox.KaskadQR;

const LEVELS = ["L", "M", "Q", "H"];
// предельное число байт: данные версии минус режим и счётчик
function capacity(ver, lvl) {
  const raw = (() => {
    let r = (16 * ver + 128) * ver + 64;
    if (ver >= 2) { const n = Math.floor(ver / 7) + 2; r -= (25 * n - 10) * n - 55; if (ver >= 7) r -= 36; }
    return r;
  })();
  const ecc = [
    [7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
  ][LEVELS.indexOf(lvl)][ver - 1];
  const blocks = [
    [1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    [1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    [1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
  ][LEVELS.indexOf(lvl)][ver - 1];
  const dataBits = (Math.floor(raw / 8) - ecc * blocks) * 8;
  return Math.floor((dataBits - 4 - (ver < 10 ? 8 : 16)) / 8);
}

let seed = 12345;
function rnd() { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; }
const ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_=+/:.,;!?@#$%^&*()[] {}~";
function randomText(n) {
  let s = "q";                                    // строчная буква — qrencode -8 всё равно держит байтовый режим
  while (s.length < n) s += ALPHABET[Math.floor(rnd() * ALPHABET.length)];
  return s.slice(0, n);
}

function reference(text, lvl, ver) {
  const out = execFileSync("qrencode", ["-8", "-l", lvl, "-v", String(ver), "-m", "0", "-s", "1", "-t", "ASCII"],
    { input: text, maxBuffer: 64 << 20 }).toString("latin1");
  const rows = out.split("\n").filter((l) => l.length > 0);
  const size = rows.length;
  const mod = new Uint8Array(size * size);
  rows.forEach((row, y) => {
    const padded = row.padEnd(size * 2, " ");
    for (let x = 0; x < size; x++) mod[y * size + x] = padded.substr(x * 2, 2) === "##" ? 1 : 0;
  });
  return { size, modules: mod };
}

function same(a, b) {
  if (a.size !== b.size) return false;
  for (let i = 0; i < a.modules.length; i++) if (a.modules[i] !== b.modules[i]) return false;
  return true;
}

const quick = process.argv.includes("--quick");
let total = 0, bad = 0, maskDiff = 0;
for (const lvl of LEVELS) {
  for (let ver = 1; ver <= 40; ver++) {
    if (quick && ver % 7 !== 1 && ver < 38) continue;
    const cap = capacity(ver, lvl);
    const sizes = [cap, Math.max(1, cap - 1 - Math.floor(rnd() * 20)), Math.max(1, Math.floor(cap * rnd()))];
    for (const n of sizes) {
      const text = randomText(n);
      total++;
      const ref = reference(text, lvl, ver);
      const mine = QR.encode(text, lvl);
      if (mine.version < ver) {                 // qrencode с -v берёт «не меньше»; при n < вместимости версии мало — нормально
        // меньшая версия у нас — значит qrencode поднял версию по -v; сравниваем с нашей версией
      }
      if (mine.version !== (ref.size - 17) / 4) {
        // qrencode взял версию по -v, а n помещается в меньшую — перезапросим без -v
        const out = execFileSync("qrencode", ["-8", "-l", lvl, "-m", "0", "-s", "1", "-t", "ASCII"], { input: text, maxBuffer: 64 << 20 }).toString("latin1");
        const rows = out.split("\n").filter((l) => l.length > 0);
        const size = rows.length;
        const mod = new Uint8Array(size * size);
        rows.forEach((row, y) => { const p = row.padEnd(size * 2, " "); for (let x = 0; x < size; x++) mod[y * size + x] = p.substr(x * 2, 2) === "##" ? 1 : 0; });
        ref.size = size; ref.modules = mod;
      }
      if (same(ref, mine)) continue;
      let anyMask = -1;
      for (let m = 0; m < 8; m++) {
        if (same(ref, QR.encode(text, lvl, m))) { anyMask = m; break; }
      }
      if (anyMask >= 0) { maskDiff++; console.log("маска: версия %d уровень %s длина %d — у qrencode %d, у qr.js %d", mine.version, lvl, n, anyMask, mine.mask); }
      else { bad++; console.log("РАСХОЖДЕНИЕ: версия %d уровень %s длина %d", mine.version, lvl, n); }
    }
  }
}
console.log("проверено %d кодов, расхождений матрицы %d, спорных масок %d", total, bad, maskDiff);
process.exit(bad ? 1 : 0);
