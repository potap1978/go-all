/* Kaskad — кодировщик QR-кодов для панели (подгружается app.js, когда нужно показать QR выданных настроек).

   Своя реализация ISO/IEC 18004 (QR Code, модель 2): байтовый режим (UTF-8), версии 1–40, уровни коррекции
   L, M, Q, H, выбор маски по штрафам, код Рида-Соломона над GF(256). Чужого кода здесь нет; таблицы блоков
   коррекции — из стандарта. Результат сверен с qrencode 4.1: все 480 кодов (160 сочетаний «версия × уровень»,
   по три строки: предельной длины и две случайные короче) совпали с ним модуль в модуль при одной и той же
   маске. Саму маску qrencode иногда выбирает другую (в пятой части случаев: у него свой подсчёт штрафов N3) —
   код от этого не хуже, читается любая из восьми масок. Сверка — tools/qr_check.js, запускается руками (нужны
   node и qrencode).

   API:
     KaskadQR.encode(text, ecc?, mask?) -> {size, modules, version, ecc, mask}
         ecc — "L" | "M" | "Q" | "H"; без него: наименьшая версия при "L", и "M", если данные влезают в ту же версию
         (3.3.3 №38: меньше модулей — крупнее точки, телефон читает с экрана легче; kaskad.qr.version_of). modules — Uint8Array size*size, 1 — тёмный модуль.
         Данные не поместились в версию 40 — бросает Error("qr-too-long").
     KaskadQR.svg(code) -> <svg> (рамка в 4 модуля; цвета — классами gx-qrart__paper и gx-qrart__ink из styles.css)
     KaskadQR.svgText(code, ink, paper) -> строка SVG-файла (для «Скачать»), цвета задаются явно. */
(function (g) {
  "use strict";

  var ECC_ORD = { L: 0, M: 1, Q: 2, H: 3 };
  var ECC_FORMAT_BITS = [1, 0, 3, 2];
  // Байт коррекции на блок и число блоков: индекс — уровень (L, M, Q, H), затем версия 1..40 (нулевой элемент не используется).
  var ECC_PER_BLOCK = [
    [-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    [-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    [-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
  ];
  var BLOCKS = [
    [-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    [-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    [-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    [-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
  ];

  function utf8(text) {
    if (typeof TextEncoder !== "undefined") return Array.prototype.slice.call(new TextEncoder().encode(text));
    var s = unescape(encodeURIComponent(text)), out = [];
    for (var i = 0; i < s.length; i++) out.push(s.charCodeAt(i));
    return out;
  }

  function rawModules(ver) {
    var r = (16 * ver + 128) * ver + 64;
    if (ver >= 2) {
      var n = Math.floor(ver / 7) + 2;
      r -= (25 * n - 10) * n - 55;
      if (ver >= 7) r -= 36;
    }
    return r;
  }

  function dataCodewords(ver, ecl) {
    return Math.floor(rawModules(ver) / 8) - ECC_PER_BLOCK[ecl][ver] * BLOCKS[ecl][ver];
  }

  function alignPositions(ver) {
    if (ver === 1) return [];
    var n = Math.floor(ver / 7) + 2, size = ver * 4 + 17;
    var step = ver === 32 ? 26 : Math.ceil((ver * 4 + 4) / (n * 2 - 2)) * 2;
    var res = [6];
    for (var pos = size - 7; res.length < n; pos -= step) res.splice(1, 0, pos);
    return res;
  }

  // GF(256), многочлен 0x11D.
  function gfMul(x, y) {
    var z = 0;
    for (var i = 7; i >= 0; i--) {
      z = (z << 1) ^ ((z >>> 7) * 0x11D);
      z ^= ((y >>> i) & 1) * x;
    }
    return z;
  }

  function rsDivisor(degree) {
    var res = [], i, j;
    for (i = 0; i < degree - 1; i++) res.push(0);
    res.push(1);
    var root = 1;
    for (i = 0; i < degree; i++) {
      for (j = 0; j < res.length; j++) {
        res[j] = gfMul(res[j], root);
        if (j + 1 < res.length) res[j] ^= res[j + 1];
      }
      root = gfMul(root, 0x02);
    }
    return res;
  }

  function rsRemainder(data, div) {
    var res = div.map(function () { return 0; });
    data.forEach(function (b) {
      var factor = b ^ res.shift();
      res.push(0);
      div.forEach(function (coef, i) { res[i] ^= gfMul(coef, factor); });
    });
    return res;
  }

  function pickVersion(bytes, ecl) {
    for (var ver = 1; ver <= 40; ver++) {
      var bits = 4 + (ver < 10 ? 8 : 16) + bytes * 8;
      if (bits <= dataCodewords(ver, ecl) * 8) return ver;
    }
    return 0;
  }

  function bitsOf(bytes, ver, ecl) {
    var bits = [];
    function put(val, len) { for (var i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1); }
    put(4, 4);
    put(bytes.length, ver < 10 ? 8 : 16);
    bytes.forEach(function (b) { put(b, 8); });
    var cap = dataCodewords(ver, ecl) * 8;
    put(0, Math.min(4, cap - bits.length));
    put(0, (8 - bits.length % 8) % 8);
    for (var pad = 0xEC; bits.length < cap; pad ^= 0xEC ^ 0x11) put(pad, 8);
    var out = [];
    for (var i = 0; i < bits.length; i += 8) {
      var v = 0;
      for (var j = 0; j < 8; j++) v = (v << 1) | bits[i + j];
      out.push(v);
    }
    return out;
  }

  function interleave(data, ver, ecl) {
    var nb = BLOCKS[ecl][ver], eccLen = ECC_PER_BLOCK[ecl][ver];
    var raw = Math.floor(rawModules(ver) / 8);
    var shortBlocks = nb - raw % nb, shortLen = Math.floor(raw / nb);
    var blocks = [], div = rsDivisor(eccLen), k = 0, i, j;
    for (i = 0; i < nb; i++) {
      var dat = data.slice(k, k + shortLen - eccLen + (i < shortBlocks ? 0 : 1));
      k += dat.length;
      var ecc = rsRemainder(dat, div);
      if (i < shortBlocks) dat.push(0);
      blocks.push(dat.concat(ecc));
    }
    var out = [];
    for (i = 0; i < blocks[0].length; i++) {
      for (j = 0; j < nb; j++) {
        if (i !== shortLen - eccLen || j >= shortBlocks) out.push(blocks[j][i]);
      }
    }
    return out;
  }

  // Сетка модулей: modules (цвета) и isFn (служебные модули — не данные).
  function Grid(ver, ecl) {
    var size = ver * 4 + 17;
    this.ver = ver;
    this.ecl = ecl;
    this.size = size;
    this.m = new Uint8Array(size * size);
    this.fn = new Uint8Array(size * size);
  }
  Grid.prototype.setFn = function (x, y, dark) {
    this.m[y * this.size + x] = dark ? 1 : 0;
    this.fn[y * this.size + x] = 1;
  };
  Grid.prototype.drawFunctionPatterns = function () {
    var size = this.size, i, j;
    for (i = 0; i < size; i++) {
      this.setFn(6, i, i % 2 === 0);
      this.setFn(i, 6, i % 2 === 0);
    }
    this.finder(3, 3);
    this.finder(size - 4, 3);
    this.finder(3, size - 4);
    var pos = alignPositions(this.ver), n = pos.length;
    for (i = 0; i < n; i++) {
      for (j = 0; j < n; j++) {
        if (!((i === 0 && j === 0) || (i === 0 && j === n - 1) || (i === n - 1 && j === 0))) this.align(pos[i], pos[j]);
      }
    }
    this.formatBits(0);
    this.versionBits();
  };
  Grid.prototype.finder = function (cx, cy) {
    for (var dy = -4; dy <= 4; dy++) {
      for (var dx = -4; dx <= 4; dx++) {
        var dist = Math.max(Math.abs(dx), Math.abs(dy)), x = cx + dx, y = cy + dy;
        if (x >= 0 && x < this.size && y >= 0 && y < this.size) this.setFn(x, y, dist !== 2 && dist !== 4);
      }
    }
  };
  Grid.prototype.align = function (cx, cy) {
    for (var dy = -2; dy <= 2; dy++) {
      for (var dx = -2; dx <= 2; dx++) this.setFn(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  };
  Grid.prototype.formatBits = function (mask) {
    var data = (ECC_FORMAT_BITS[this.ecl] << 3) | mask, rem = data, i, size = this.size;
    for (i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    var bits = ((data << 10) | rem) ^ 0x5412;
    function bit(k) { return ((bits >>> k) & 1) !== 0; }
    for (i = 0; i <= 5; i++) this.setFn(8, i, bit(i));
    this.setFn(8, 7, bit(6));
    this.setFn(8, 8, bit(7));
    this.setFn(7, 8, bit(8));
    for (i = 9; i < 15; i++) this.setFn(14 - i, 8, bit(i));
    for (i = 0; i < 8; i++) this.setFn(size - 1 - i, 8, bit(i));
    for (i = 8; i < 15; i++) this.setFn(8, size - 15 + i, bit(i));
    this.setFn(8, size - 8, true);
  };
  Grid.prototype.versionBits = function () {
    if (this.ver < 7) return;
    var rem = this.ver, i;
    for (i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
    var bits = (this.ver << 12) | rem;
    for (i = 0; i < 18; i++) {
      var dark = ((bits >>> i) & 1) !== 0, a = this.size - 11 + i % 3, b = Math.floor(i / 3);
      this.setFn(a, b, dark);
      this.setFn(b, a, dark);
    }
  };
  Grid.prototype.place = function (codewords) {
    var size = this.size, i = 0, right, vert, j;
    for (right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (vert = 0; vert < size; vert++) {
        for (j = 0; j < 2; j++) {
          var x = right - j, upward = ((right + 1) & 2) === 0, y = upward ? size - 1 - vert : vert;
          if (!this.fn[y * size + x] && i < codewords.length * 8) {
            this.m[y * size + x] = (codewords[i >>> 3] >>> (7 - (i & 7))) & 1;
            i++;
          }
        }
      }
    }
  };
  Grid.prototype.applyMask = function (mask) {
    var size = this.size, x, y, inv;
    for (y = 0; y < size; y++) {
      for (x = 0; x < size; x++) {
        switch (mask) {
          case 0: inv = (x + y) % 2 === 0; break;
          case 1: inv = y % 2 === 0; break;
          case 2: inv = x % 3 === 0; break;
          case 3: inv = (x + y) % 3 === 0; break;
          case 4: inv = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break;
          case 5: inv = x * y % 2 + x * y % 3 === 0; break;
          case 6: inv = (x * y % 2 + x * y % 3) % 2 === 0; break;
          default: inv = ((x + y) % 2 + x * y % 3) % 2 === 0;
        }
        if (inv && !this.fn[y * size + x]) this.m[y * size + x] ^= 1;
      }
    }
  };
  Grid.prototype.penalty = function () {
    var size = this.size, m = this.m, res = 0, x, y, k;
    function runs(get) {
      var color = 0, run = 0, hist = [0, 0, 0, 0, 0, 0, 0], p = 0;
      function addHist(len) {
        if (hist[0] === 0) len += size;
        hist.pop();
        hist.unshift(len);
      }
      function patterns() {
        var n = hist[1], core = n > 0 && hist[2] === n && hist[3] === n * 3 && hist[4] === n && hist[5] === n;
        return (core && hist[0] >= n * 4 && hist[6] >= n ? 1 : 0) + (core && hist[6] >= n * 4 && hist[0] >= n ? 1 : 0);
      }
      for (var i = 0; i < size; i++) {
        if (get(i) === color) {
          run++;
          if (run === 5) p += 3;
          else if (run > 5) p++;
        } else {
          addHist(run);
          if (!color) p += patterns() * 40;
          color = get(i);
          run = 1;
        }
      }
      // конец ряда: закрываем последнюю серию
      if (color) { addHist(run); run = 0; }
      run += size;
      addHist(run);
      p += patterns() * 40;
      return p;
    }
    for (y = 0; y < size; y++) res += runs(function (i) { return m[y * size + i]; });
    for (x = 0; x < size; x++) res += runs(function (i) { return m[i * size + x]; });
    for (y = 0; y < size - 1; y++) {
      for (x = 0; x < size - 1; x++) {
        var c = m[y * size + x];
        if (c === m[y * size + x + 1] && c === m[(y + 1) * size + x] && c === m[(y + 1) * size + x + 1]) res += 3;
      }
    }
    var dark = 0;
    for (k = 0; k < m.length; k++) dark += m[k];
    var total = size * size;
    res += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * 10;
    return res;
  };

  function encode(text, ecc, forceMask) {
    var bytes = utf8(String(text == null ? "" : text));
    var level, ecl, ver;
    if (ecc && ECC_ORD[ecc] !== undefined) {
      level = ecc; ecl = ECC_ORD[level]; ver = pickVersion(bytes.length, ecl);
    } else {                                    // автовыбор (3.3.3 №38): наименьшая версия при «L», «M» — если влез в ту же
      ver = pickVersion(bytes.length, 0);       // (экран не пачкается: камере важен размер модуля, а не коррекция);
      level = ver && pickVersion(bytes.length, 1) === ver ? "M" : "L";   // так же kaskad.qr.version_of и /p/
      ecl = ECC_ORD[level];
    }
    if (!ver) throw new Error("qr-too-long");
    var words = interleave(bitsOf(bytes, ver, ecl), ver, ecl);
    var grid = new Grid(ver, ecl);
    grid.drawFunctionPatterns();
    grid.place(words);
    var mask = forceMask, best = Infinity, i;
    if (mask === undefined || mask === null) {
      for (i = 0; i < 8; i++) {
        grid.applyMask(i);
        grid.formatBits(i);
        var p = grid.penalty();
        if (p < best) { best = p; mask = i; }
        grid.applyMask(i);                      // XOR второй раз — маска снята
      }
    }
    grid.applyMask(mask);
    grid.formatBits(mask);
    return { size: grid.size, modules: grid.m, version: ver, ecc: level, mask: mask };
  }

  var NS = "http://www.w3.org/2000/svg";
  var QUIET = 4;

  // Тёмные модули одним контуром: подряд идущие в строке сливаются в прямоугольник.
  function pathOf(code, q) {
    var d = "", n = code.size, r, c, e;
    q = q == null ? QUIET : q;
    for (r = 0; r < n; r++) {
      for (c = 0; c < n; c++) {
        if (!code.modules[r * n + c]) continue;
        e = c + 1;
        while (e < n && code.modules[r * n + e]) e++;
        d += "M" + (c + q) + " " + (r + q) + "h" + (e - c) + "v1h-" + (e - c) + "z";
        c = e;
      }
    }
    return d;
  }

  // quiet — поле в модулях (по умолчанию QUIET; QR адреса страницы в «QR слишком большой» — 2, 3.3.3 №38)
  function svg(code, label, quiet) {
    var q = typeof quiet === "number" && quiet >= 0 ? quiet : QUIET;
    var size = code.size + q * 2;
    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 " + size + " " + size);
    s.setAttribute("role", "img");
    if (label) s.setAttribute("aria-label", label);
    var paper = document.createElementNS(NS, "rect");
    paper.setAttribute("class", "gx-qrart__paper");
    paper.setAttribute("width", String(size));
    paper.setAttribute("height", String(size));
    var ink = document.createElementNS(NS, "path");
    ink.setAttribute("class", "gx-qrart__ink");
    ink.setAttribute("shape-rendering", "crispEdges");
    ink.setAttribute("d", pathOf(code, q));
    s.appendChild(paper);
    s.appendChild(ink);
    return s;
  }

  function svgText(code, ink, paper) {
    var size = code.size + QUIET * 2;
    return '<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="' + NS + '" viewBox="0 0 ' + size + " " + size +
      '" width="' + size * 8 + '" height="' + size * 8 + '" shape-rendering="crispEdges">' +
      '<rect width="' + size + '" height="' + size + '" fill="' + (paper || "#ffffff") + '"/>' +
      '<path d="' + pathOf(code) + '" fill="' + (ink || "#000000") + '"/></svg>\n';
  }

  g.KaskadQR = { encode: encode, svg: svg, svgText: svgText };
})(typeof window !== "undefined" ? window : globalThis);
