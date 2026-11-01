# -*- coding: utf-8 -*-
"""Вёрстка страницы подключения в настоящем браузере — Chrome без окна по протоколу DevTools (PAGE-LAY-01).

Владелец 05.10.2026 (iPhone → incy, две колонки): две карточки QR разной высоты, правая и её подпись упирались в край
карточки шага. Здесь служба страниц с подставным мостом (как tests/test_pages_server.py) открывается в Chrome на ширинах
360, 400, 768, 1024, 1280 и 1440 px, в светлой и тёмной теме, и на вкладке «Подписка» перебираются все устройства и все
приложения с открытым QR, на «Статическом ключе» — QR ссылок приложений и QR ключей. Для каждого состояния — DOM-проверка:
ни один элемент карточки шага не выходит за её край, у страницы нет горизонтальной прокрутки, два QR рядом — одной высоты
с подписями на одной линии. Нажатия — настоящими событиями мыши и клавиатуры (Input.dispatch*): страница принимает только
их (isTrusted). Два QR рядом (qrPair): активен один, второй размыт (blur не меньше 8 px) и для экранного чтеца — кнопка;
щелчок, наведение и фокус клавиатуры делают его активным; при prefers-reduced-motion — без перехода.

Страница подтверждения короткой ссылки (SHORT-03, ShortConfirmBrowserTests): после «Вход подтверждён» вкладка сама
никуда не переходит, полную ссылку подписки (у awgNN.vpn — ключ и его QR, у .conf — «Скачать файл») даёт только
настоящее нажатие; сменившийся после подтверждения адрес сети (X-Real-IP вкладки) ссылку получает, вкладка без своего
одноразового значения и нажатие после окна в 15 минут — «Нужна новая проверка» без нового запроса в бот; вёрстка
ожидания, итога до и после нажатия и «Нужна новая проверка» — на тех же ширинах и в обеих темах.

Нет Chrome (на сервере его нет) — проверки пропускаются, как отрисовка в node у tests/test_pages_dryrun.sh. Свой браузер —
GA_CHROME=<путь>.

Запуск: python3 -m unittest tests/test_pages_layout.py
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_pages_server as T  # noqa: E402

WIDTHS = (360, 400, 768, 1024, 1280, 1440)
THEMES = ("light", "dark")


def chrome_bin():
    """Chrome или Chromium: GA_CHROME, приложение macOS, имя в PATH. Нет — None."""
    cands = [os.environ.get("GA_CHROME", ""), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    cands += [shutil.which(n) or "" for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


CHROME = chrome_bin()


def png_b64(n=29, scale=8):
    """Настоящий PNG «как QR» (шахматка модулей) — у карточек ключей появляются кнопки QR, картинки грузятся."""
    size = n * scale
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            row.append(0 if ((x // scale) + (y // scale)) % 2 else 255)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)) +
           chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


# ── WebSocket (RFC 6455) — ровно столько, сколько нужно протоколу DevTools ────
class WS(object):
    def __init__(self, url, timeout=60):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                           % (u.path, u.hostname, u.port, key)).encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise IOError("websocket: обрыв при рукопожатии")
            buf += chunk
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise IOError("websocket: %r" % head[:200])

    def _frame(self, op, data):
        hdr = bytearray([0x80 | op])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(hdr) + mask + body)

    def send(self, text):
        self._frame(1, text.encode("utf-8"))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise IOError("websocket: соединение закрыто")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._read(2)
            n = b1 & 0x7f
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b1 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            op = b0 & 0x0f
            if op == 9:
                self._frame(10, data)
                continue
            if op == 8:
                raise IOError("websocket: браузер закрыл соединение")
            parts.append(data)
            if b0 & 0x80:
                return b"".join(parts).decode("utf-8")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Browser(object):
    """Chrome без окна и одна вкладка: вызовы протокола DevTools, нажатия настоящими событиями ввода."""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="ga-layout.")
        self.proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=0", "--user-data-dir=" + self.tmp,
                                      "--no-first-run", "--no-default-browser-check", "--disable-gpu",
                                      "--disable-extensions", "--hide-scrollbars", "--mute-audio", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file, deadline, port = os.path.join(self.tmp, "DevToolsActivePort"), time.time() + 30, 0
        while not port:
            if os.path.isfile(port_file):
                with open(port_file) as fh:
                    first = fh.read().split()
                port = int(first[0]) if first else 0
            if not port:
                if time.time() > deadline or self.proc.poll() is not None:
                    self.quit()
                    raise RuntimeError("Chrome не поднялся")
                time.sleep(0.1)
        targets, deadline = [], time.time() + 30
        while not targets:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=10) as r:
                targets = [t for t in json.load(r) if t.get("type") == "page"]
            if not targets:
                if time.time() > deadline:
                    raise RuntimeError("у Chrome нет вкладки")
                time.sleep(0.1)
        self.ws = WS(targets[0]["webSocketDebuggerUrl"])
        self.n = 0
        self.call("Page.enable")
        self.call("Runtime.enable")

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError("%s: %s" % (method, msg["error"]))
                return msg.get("result") or {}

    def js(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError("JS: %s" % json.dumps(r["exceptionDetails"], ensure_ascii=False)[:600])
        return (r.get("result") or {}).get("value")

    def viewport(self, width, height=900):
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1,
                  mobile=width < 600)

    def media(self, theme="light", reduce_motion=False):
        self.call("Emulation.setEmulatedMedia", features=[
            {"name": "prefers-color-scheme", "value": theme},
            {"name": "prefers-reduced-motion", "value": "reduce" if reduce_motion else "no-preference"}])

    def goto(self, url, ready="document.querySelector('.pg-tabs')"):
        self.call("Page.navigate", url=url)
        self.wait(ready, 20)

    def wait(self, cond, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js("!!(%s)" % cond):
                return True
            time.sleep(0.03)
        return False

    def settle(self, ms=15):
        self.js("new Promise(function (r) { requestAnimationFrame(function () { setTimeout(r, %d); }); })" % ms)

    def point(self, el_js):
        return self.js("(function () { var el = %s; if (!el) { return null; } el.scrollIntoView({block: 'center', "
                       "inline: 'nearest'}); var b = el.getBoundingClientRect(); return [b.left + b.width / 2, "
                       "b.top + Math.min(b.height / 2, 20)]; })()" % el_js)

    def click(self, el_js):
        xy = self.point(el_js)
        if not xy:
            return False
        x, y = xy
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        self.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=1)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=1)
        return True

    def hover(self, el_js):
        xy = self.point(el_js)
        if xy:
            self.call("Input.dispatchMouseEvent", type="mouseMoved", x=xy[0], y=xy[1])
        return bool(xy)

    def key(self, key, code, vk):
        for kind in ("rawKeyDown", "keyUp"):
            self.call("Input.dispatchKeyEvent", type=kind, key=key, code=code, windowsVirtualKeyCode=vk,
                      nativeVirtualKeyCode=vk)

    def quit(self):
        try:
            self.ws.close()
        except AttributeError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.tmp, True)


# Выход за край: каждый видимый элемент карточки шага (и карточек внутри) — в её границах; внутри прокручиваемых по
# горизонтали и обрезающих блоков — в границах такого блока. Плюс у страницы нет горизонтальной прокрутки.
MEASURE_JS = r"""
(function () {
  var out = [], de = document.documentElement, vw = de.clientWidth;
  function name(el) {
    var c = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    var txt = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 30);
    return el.tagName.toLowerCase() + (c ? '.' + c : '') + (txt ? ' «' + txt + '»' : '');
  }
  if (de.scrollWidth > vw + 1) { out.push('страница шире окна: ' + de.scrollWidth + ' > ' + vw); }
  document.querySelectorAll('.pg-step, .ga-card').forEach(function (card) {
    var cb = card.getBoundingClientRect();
    card.querySelectorAll('*').forEach(function (el) {
      var r = el.getBoundingClientRect(), box = cb, p, cs;
      if (!r.width || !r.height) { return; }
      cs = getComputedStyle(el);
      if (cs.position === 'fixed' || cs.visibility === 'hidden') { return; }
      for (p = el.parentElement; p && p !== card; p = p.parentElement) {
        var ox = getComputedStyle(p).overflowX;
        if (ox !== 'visible') { box = p.getBoundingClientRect(); break; }
      }
      if (r.left < box.left - 1 || r.right > box.right + 1) {
        out.push(name(el) + ' [' + Math.round(r.left) + '..' + Math.round(r.right) + '] за краем ' + name(card).slice(0, 40) +
                 ' [' + Math.round(box.left) + '..' + Math.round(box.right) + ']');
      }
    });
  });
  document.querySelectorAll('.pg-qr2').forEach(function (pair) {
    var ps = [].slice.call(pair.children).filter(function (p) { return p.classList.contains('pg-qr'); });
    if (ps.length !== 2) { out.push('в паре QR не две карточки: ' + ps.length); return; }
    var a = ps[0].getBoundingClientRect(), b = ps[1].getBoundingClientRect();
    var ca = ps[0].querySelector('.pg-qr__cap').getBoundingClientRect(), cb2 = ps[1].querySelector('.pg-qr__cap').getBoundingClientRect();
    if (Math.abs(a.height - b.height) > 1) { out.push('две карточки QR разной высоты: ' + Math.round(a.height) + ' и ' + Math.round(b.height)); }
    if (Math.abs(a.top - b.top) < 1 && Math.abs(ca.top - cb2.top) > 1) { out.push('подписи QR не на одной линии: ' + Math.round(ca.top) + ' и ' + Math.round(cb2.top)); }
    var ia = ps[0].querySelector('.pg-qr__img').getBoundingClientRect(), ib = ps[1].querySelector('.pg-qr__img').getBoundingClientRect();
    if (Math.abs(ia.width - ib.width) > 1) { out.push('картинки QR разной ширины: ' + Math.round(ia.width) + ' и ' + Math.round(ib.width)); }
  });
  return out;
})()
"""


@unittest.skipUnless(CHROME, "нет Chrome — проверки вёрстки пропущены")
class PageLayoutTests(unittest.TestCase):
    """Все устройства × все приложения вкладки «Подписка» и QR «Статического ключа» на шести ширинах и в двух темах."""

    @classmethod
    def setUpClass(cls):
        cls.stand = make_stand()
        cls.url = "http://127.0.0.1:%d/p-%s/" % (cls.stand.port, cls.stand.PAGE)
        try:
            cls.b = Browser()
        except Exception:
            cls.stand.tearDown()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.b.quit()
        cls.stand.tearDown()

    def open(self, width, theme, tab):
        b = self.b
        b.viewport(width)
        b.media(theme)
        b.goto(self.url)
        b.click("document.getElementById('pg-tab-%s')" % tab)
        b.wait("document.getElementById('pg-tab-%s').getAttribute('aria-selected') === 'true'" % tab)
        b.settle()

    def devices(self):
        return self.b.js("[].map.call(document.querySelectorAll('.pg-devs .pg-dev'), function (d) { return d.textContent.trim(); })")

    def apps(self):
        return self.b.js("[].map.call(document.querySelectorAll('.pg-apps .pg-app__head'), function (h) { "
                         "return h.querySelector('.pg-app__name b').textContent; })")

    def pick(self, kind, text):
        sel = {"dev": "'.pg-devs .pg-dev'", "app": "'.pg-apps .pg-app__head'"}[kind]
        expr = ("[].filter.call(document.querySelectorAll(%s), function (e) { return (%s).trim() === %s; })[0]"
                % (sel, "e.querySelector('.pg-app__name b').textContent" if kind == "app" else "e.textContent",
                   json.dumps(text)))
        self.assertTrue(self.b.click(expr), (kind, text))
        self.b.settle()

    def measure(self, where):
        return ["%s: %s" % (where, m) for m in self.b.js(MEASURE_JS)]

    def sub_tab(self, theme):
        bad, states = [], 0
        for width in WIDTHS:
            self.open(width, theme, "sub")
            for dev in self.devices():
                self.pick("dev", dev)
                for app in self.apps():
                    self.pick("app", app)
                    where = "%d px %s, «Подписка», %s → %s" % (width, theme, dev, app)
                    bad += self.measure(where)
                    qr = "document.querySelector('.pg-trio .pg-tri')"
                    if self.b.js("!!%s && !%s.disabled" % (qr, qr)):
                        self.b.click(qr)
                        self.b.wait("document.querySelector('.pg-out .pg-qr, .pg-out .pg-kn')", 3)
                        self.b.settle(60)
                        bad += self.measure(where + " (QR)")
                    states += 1
        return bad, states

    def test_subscription_tab_light_no_overflow(self):
        bad, states = self.sub_tab("light")
        self.assertGreater(states, 100)
        self.assertEqual(bad[:40], [], "%d нарушений" % len(bad))

    def test_subscription_tab_dark_no_overflow(self):
        bad, states = self.sub_tab("dark")
        self.assertGreater(states, 100)
        self.assertEqual(bad[:40], [], "%d нарушений" % len(bad))

    def test_static_tab_qr_blocks_no_overflow(self):
        # Каждое устройство × приложение — с открытым QR первой ссылки (магазин или страница выпусков); QR ключей
        # (vpn:// и .conf всех профилей) — по разу на ширину и тему: карточки ключей у приложений одинаковые
        bad, opened = [], 0
        link_qr = "document.querySelector('.pg-app[data-on=\"1\"] .pg-link .pg-btn--qr')"
        key_qr = "document.querySelectorAll('.pg-kbtn[aria-pressed]')"
        for theme in THEMES:
            for width in WIDTHS:
                self.open(width, theme, "static")
                keys_done = False
                for dev in self.devices():
                    self.pick("dev", dev)
                    for app in self.apps():
                        self.pick("app", app)
                        where = "%d px %s, «Статический ключ», %s → %s" % (width, theme, dev, app)
                        bad += self.measure(where)
                        if self.b.click(link_qr):
                            self.b.settle()
                            bad += self.measure(where + " (QR ссылки)")
                            opened += 1
                        n = 0 if keys_done else (self.b.js(key_qr + ".length") or 0)
                        for i in range(n):
                            if self.b.click(key_qr + "[%d]" % i):
                                self.b.settle()
                                bad += self.measure(where + " (QR ключа %d)" % (i + 1))
                                opened += 1
                                keys_done = True
        self.assertGreater(opened, 50)
        self.assertEqual(bad[:40], [], "%d нарушений" % len(bad))

    def test_qr_pair_click_hover_focus_and_reduced_motion(self):
        b = self.b
        self.open(1280, "light", "sub")
        self.pick("dev", "iPhone и iPad")
        self.pick("app", "incy")
        b.click("document.querySelector('.pg-trio .pg-tri')")
        self.assertTrue(b.wait("document.querySelector('.pg-qr2')", 3))
        b.settle(350)
        state = ("[].map.call(document.querySelectorAll('.pg-qr2 > .pg-qr'), function (p) { var img = p.querySelector('img'); "
                 "var f = getComputedStyle(img).filter, m = /blur\\(([0-9.]+)px\\)/.exec(f); return [p.getAttribute('data-qr'), "
                 "p.getAttribute('role'), img.getAttribute('aria-hidden') || '', m ? Number(m[1]) : 0, "
                 "Number(getComputedStyle(p.querySelector('.pg-qr__veil')).opacity), p.getAttribute('aria-label') || '']; })")
        def active():
            return [s[0] for s in b.js(state)]
        s = b.js(state)
        self.assertEqual([x[0] for x in s], ["on", "off"], "по умолчанию активен «Камерой телефона»")
        self.assertTrue(s[0][5].startswith("Камерой телефона"), s[0])
        self.assertEqual((s[1][1], s[1][2]), ("button", "true"), "размытый — кнопка, картинка скрыта от чтеца")
        self.assertGreaterEqual(s[1][3], 8, "blur не меньше 8 px")
        self.assertEqual((s[0][3], s[1][4], s[0][4]), (0, 1, 0), "подпись поверх — только у размытого")
        self.assertTrue(s[1][5].startswith("Показать этот код: Сканером в incy"), s[1])
        # Наведение мыши — второй активен, первый размыт
        b.hover("document.querySelectorAll('.pg-qr2 > .pg-qr')[1]")
        b.settle(350)
        self.assertEqual(active(), ["off", "on"], "наведение")
        # Фокус клавиатуры: Tab с кнопки «Скопировать» — на первый QR
        b.js("[].filter.call(document.querySelectorAll('.pg-tri'), function (x) { return /Скопировать/.test(x.textContent); })[0].focus()")
        b.call("Input.dispatchMouseEvent", type="mouseMoved", x=1, y=1)
        b.key("Tab", "Tab", 9)
        b.settle(350)
        self.assertEqual(b.js("document.activeElement.getAttribute('data-qr')"), "on")
        self.assertEqual(active(), ["on", "off"], "фокус клавиатуры")
        b.key("Tab", "Tab", 9)
        b.settle(350)
        self.assertEqual(active(), ["off", "on"], "фокус клавиатуры на втором")
        # Щелчок по размытому
        b.click("document.querySelectorAll('.pg-qr2 > .pg-qr')[0]")
        b.settle(350)
        self.assertEqual(active(), ["on", "off"], "щелчок")
        # Переход плавный, а при prefers-reduced-motion — без перехода
        tr = "getComputedStyle(document.querySelector('.pg-qr2 .pg-qr__img img')).transitionDuration"
        self.assertTrue(any(float(x.strip().rstrip("s") or 0) >= 0.2 for x in b.js(tr).split(",")), b.js(tr))
        b.media("light", reduce_motion=True)
        b.settle()
        self.assertTrue(all(float(x.strip().rstrip("s") or 0) <= 0.001 for x in b.js(tr).split(",")), b.js(tr))
        b.media("light")


@unittest.skipUnless(CHROME, "нет Chrome — страница подтверждения короткой ссылки в браузере не проверена")
class ShortConfirmBrowserTests(unittest.TestCase):
    """SHORT-03 в настоящем браузере (владелец 05.10): после «Вход подтверждён» страница подтверждения короткой ссылки
    сама никуда не переходит — адрес вкладки становится ?r=<запрос>, а полную ссылку подписки этому устройству даёт
    только настоящее нажатие «Показать полную ссылку» (isTrusted): POST /<КОД> {request, nonce}. Нажатие из скрипта
    ничего не просит. Адрес сети сменился после подтверждения (здесь — другой X-Real-IP у запросов вкладки, как за
    nginx) — эта вкладка ссылку всё равно получает, и перезагрузка её не теряет (значение — в sessionStorage); вкладка без
    своего значения и нажатие после окна в 15 минут — «Нужна новая проверка» без нового запроса в бот. awgNN.vpn — сам
    ключ vpn:// и его QR, .conf — «Скачать файл». Вёрстка ожидания, итога до и после нажатия и «Нужна новая проверка» —
    на 360–1440 px в обеих темах."""

    REVEAL = "document.getElementById('sc-reveal')"
    READY = "!document.getElementById('sc-reveal').hidden"

    @classmethod
    def setUpClass(cls):
        cls.stand = make_short_stand()
        cls.base = "http://127.0.0.1:%d" % cls.stand.port
        try:
            cls.b = Browser()
            cls.b.call("Network.enable")
            # .conf — файлом: в тесте скачивание запрещено (в папку «Загрузки» ничего не ляжет)
            for method in ("Browser.setDownloadBehavior", "Page.setDownloadBehavior"):
                try:
                    cls.b.call(method, behavior="deny")
                    break
                except RuntimeError:
                    continue
        except Exception:
            cls.stand.tearDown()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.b.quit()
        cls.stand.tearDown()

    def setUp(self):
        # Глазами службы: переходы с меткой ?go= (ShortLinks.visit, after) и запросы полной ссылки (ShortLinks.confirmed —
        # его зовёт только POST /<КОД>), по какому номеру запроса
        short = self.stand.application.short
        visit, confirmed = type(short).visit, type(short).confirmed
        self.marks, self.asks = [], []

        def visit_spy(*args, **kw):
            if kw.get("after"):
                self.marks.append(kw["after"])
            return visit(short, *args, **kw)

        def confirmed_spy(raw, rid, *args, **kw):
            self.asks.append(rid)
            return confirmed(short, raw, rid, *args, **kw)
        short.visit, short.confirmed = visit_spy, confirmed_spy
        self.addCleanup(delattr, short, "visit")
        self.addCleanup(delattr, short, "confirmed")
        self.b.viewport(400)
        self.b.media("light")

    def ip(self, addr):
        self.b.call("Network.setExtraHTTPHeaders", headers={"X-Real-IP": addr})

    def until(self, cond, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.b.js("!!(%s)" % cond):
                    return True
            except RuntimeError:
                pass                     # вкладка сменила страницу посреди вызова
            time.sleep(0.05)
        return False

    def go(self, path, cond):
        # Метка на прежнем документе: условие проверяется только на новом (тот же адрес — тоже новая загрузка)
        try:
            self.b.js("document.documentElement.setAttribute('data-was', '')")
        except RuntimeError:
            pass
        self.b.call("Page.navigate", url=self.base + path)
        self.assertTrue(self.until("!document.documentElement.hasAttribute('data-was') && (%s)" % cond, 15),
                        "%s: не дождались %s" % (path, cond))

    def confirm_page(self, display):
        """Вход по коду: страница ожидания с адреса вкладки — номер запроса, число и одноразовое значение в разметке."""
        self.go("/" + display, "document.querySelector('#sc[data-state=\"pending\"]')")
        self.assertRegex(self.b.js("document.getElementById('sc').getAttribute('data-n')") or "", r"^[A-Za-z0-9_-]{32}$")
        return (self.b.js("document.getElementById('sc').getAttribute('data-r')"),
                self.b.js("document.querySelector('.pg-sc__num').textContent"))

    def confirmed_tab(self, suffix, addr, decided=None):
        """Код пары, вход с адреса addr, администратор нажал верное число; опрос вкладки увидел «Вход подтверждён»."""
        code = self.stand.short.ensure("anna", suffix)
        self.ip(addr)
        rid, number = self.confirm_page(code["display"])
        self.stand.short.decide(rid, number, "bot:111", now=decided)
        self.assertTrue(self.until(self.READY, 12), self.visible())
        return code["display"], rid

    def press(self, cond="document.querySelector('#sc-full > *')"):
        """Настоящее нажатие «Показать полную ссылку» и ответ службы на странице."""
        self.assertTrue(self.b.click(self.REVEAL))
        self.assertTrue(self.until("(%s) || !document.querySelector('[data-sc=\"moved\"]').hidden" % cond, 10),
                        self.visible())

    def confirms(self):
        return len([e for e in self.stand.events() if e["event"] == "short_confirm"])

    def full(self, suffix):
        _s, _h, body = self.stand.request("GET", "/s-%s/%s" % (self.stand.SUB, suffix), agent="clash-verge/v2.4.2")
        return body.decode("utf-8").strip()

    def url(self, suffix):
        return "https://%s/s-%s/%s" % (T.SUB_HOST, self.stand.SUB, suffix)

    def text(self, el_js):
        return self.b.js("(function () { var el = %s; return el ? el.textContent : null; })()" % el_js)

    def visible(self):
        return self.b.js("[].filter.call(document.querySelectorAll('[data-sc]'), function (e) { return !e.hidden; })"
                         ".map(function (e) { return e.getAttribute('data-sc'); })")

    def images_loaded(self, n):
        return self.until("(function () { var l = document.querySelectorAll('#sc-full .pg-qr img'); return l.length === "
                          "%d && [].every.call(l, function (i) { return i.complete && i.naturalWidth > 0; }); })()" % n, 5)

    def test_after_ok_no_navigation_and_the_full_link_only_by_a_real_press(self):
        b, short = self.b, self.stand.short
        code = short.ensure("anna", "all.txt")
        self.ip("198.51.100.41")
        rid, number = self.confirm_page(code["display"])
        page = b.js("document.documentElement.outerHTML")
        self.assertNotIn("/s-", page, "до подтверждения — ни полного адреса")
        self.assertNotIn(self.stand.SUB, page, "…ни секрета")
        self.assertTrue(b.js(self.REVEAL + ".hidden"), "кнопку открывает только «Вход подтверждён»")
        before = self.confirms()
        short.decide(rid, number, "bot:111")
        # Опрос (раз в 2 с) видит «Вход подтверждён»: кнопка открыта, адрес вкладки — ?r=<запрос> (перезагрузка — тот же
        # итог), и ни сразу, ни через 3 с страница никуда не переходит
        self.assertTrue(self.until(self.READY, 12), self.visible())
        self.assertEqual(self.visible(), ["ok"])
        here = "/%s?r=%s&lang=ru" % (code["display"], rid)
        self.assertEqual(b.js("location.pathname + location.search"), here)
        time.sleep(3)
        self.assertEqual((b.js("location.pathname + location.search"), b.js("document.contentType"), self.visible()),
                         (here, "text/html", ["ok"]), "автоперехода нет")
        self.assertEqual((self.marks, self.asks), ([], []))
        # Нажатие из скрипта (isTrusted: false) — ни POST, ни загрузки
        b.js(self.REVEAL + ".click()")
        time.sleep(1)
        self.assertEqual((self.asks, b.js("document.getElementById('sc-full').children.length")), ([], 0),
                         "нажатие из скрипта ничего не просит")
        self.assertFalse(b.js("[].some.call(document.querySelectorAll('[data-full]'), function (e) { return !e.hidden; })"))
        # Настоящее нажатие — полная ссылка: адрес, «Скопировать», «Открыть в incy» (incy есть и на macOS), QR
        self.press("document.getElementById('sc-url')")
        url = self.url("all.txt")
        self.assertEqual(self.text("document.getElementById('sc-url')"), url)
        self.assertEqual(self.asks, [rid], "один POST — по номеру этого запроса")
        self.assertTrue(b.js(self.REVEAL + ".hidden"), "кнопка сделала своё")
        self.assertEqual(b.js("document.querySelector('#sc-full a[href^=\"incy://add/\"]').getAttribute('href')"),
                         "incy://add/" + url)
        self.assertEqual(self.text("document.querySelector('#sc-full a[href^=\"incy://\"]')"), "Открыть в incy")
        self.assertEqual(self.text("document.querySelector('#sc-full [data-copy=\"sc-url\"]')"), "Скопировать")
        if self.stand.application.qr.available():
            self.assertTrue(self.images_loaded(1), "QR полной ссылки — картинкой data: (CSP img-src data:)")
        # Следующее одноразовое значение — во вкладке (sessionStorage), не в адресе
        nxt = b.js("sessionStorage.getItem('ga-sc-n:%s')" % rid)
        self.assertRegex(nxt or "", r"^[A-Za-z0-9_-]{32}$")
        self.assertNotIn(nxt, b.js("location.href"))
        self.assertEqual((self.marks, self.confirms()), ([], before), "ни перехода, ни нового запроса в бот")
        # «Показать содержимое» — только по нажатию: то же, что получит приложение
        b.click("document.getElementById('sc-content')")
        self.assertTrue(self.until("location.search === '?go=%s' && document.contentType === 'text/plain'" % rid, 10),
                        b.js("location.href"))
        self.assertEqual(b.js("document.body.innerText").strip(), self.full("all.txt"))
        self.assertEqual(self.marks, [rid])

    def test_changed_address_after_ok_gets_the_link_and_a_tab_without_its_value_a_new_check(self):
        b, short = self.b, self.stand.short
        display, rid = self.confirmed_tab("all.yaml", "198.51.100.42")
        before = self.confirms()
        # Телефон сменил сеть (IPv4 → IPv6) уже после подтверждения: эта вкладка свою полную ссылку всё равно получает
        self.ip("2001:db8:42::1")
        self.press("document.getElementById('sc-url')")
        self.assertEqual(self.text("document.getElementById('sc-url')"), self.url("all.yaml"))
        self.assertEqual(b.js("[].map.call(document.querySelectorAll('#sc-full a.pg-btn'), function (a) { "
                              "return a.textContent; })"),
                         ["Открыть в FlClash", "Открыть в Clash Verge Rev", "Открыть в Clash Party (Mihomo Party)"],
                         "клиенты mihomo для macOS с deeplink")
        # Перезагрузка из новой сети: служба значения не даёт (адрес не тот, что начинал вход), но оно — во вкладке
        here = "/%s?r=%s&lang=ru" % (display, rid)
        self.go(here, self.READY)
        self.assertFalse(b.js("document.getElementById('sc').hasAttribute('data-n')"), "из другой сети — без значения")
        self.press("document.getElementById('sc-url')")
        self.assertEqual(self.text("document.getElementById('sc-url')"), self.url("all.yaml"))
        # Вкладка без своего значения (sessionStorage очищен; адрес страницы открыли на другом устройстве) —
        # «Нужна новая проверка», и сама никуда не переходит
        b.js("sessionStorage.clear()")
        self.go(here, "!document.querySelector('[data-sc=\"moved\"]').hidden")
        self.assertEqual(self.visible(), ["moved"])
        self.assertEqual(self.text("document.querySelector('[data-sc=\"moved\"] .pg-sc__big')"), "Нужна новая проверка")
        self.assertEqual(b.js("document.querySelector('[data-sc=\"moved\"] a').getAttribute('href')"), "/" + display)
        time.sleep(1.5)
        self.assertEqual(b.js("location.pathname + location.search"), here, "сама никуда не переходит")
        self.assertEqual((self.asks, self.marks), ([rid, rid], []))
        self.assertEqual((self.confirms(), short.pending()), (before, []), "в бот — ничего нового")

    def test_full_link_window_15_minutes_then_a_new_check(self):
        b = self.b
        before = self.confirms()
        # Вкладка простояла 15 минут после «Вход подтверждён»: кнопка есть, но нажатие — «Нужна новая проверка»
        display, rid = self.confirmed_tab("all.vpn", "198.51.100.47",
                                          decided=time.time() - T.ps.shortlinks.FULL_TTL - 5)
        self.press()
        self.assertEqual(self.visible(), ["moved"])
        self.assertEqual((self.asks, b.js("document.getElementById('sc-full').children.length")), ([rid], 0))
        self.assertIsNone(b.js("sessionStorage.getItem('ga-sc-n:%s')" % rid), "значение больше не нужно")
        # Перезагрузка — тоже «Нужна новая проверка», без значения и без нового запроса в бот
        self.go("/%s?r=%s&lang=ru" % (display, rid), "!document.querySelector('[data-sc=\"moved\"]').hidden")
        self.assertFalse(b.js("document.getElementById('sc').hasAttribute('data-n')"))
        self.assertEqual((self.confirms(), self.stand.short.pending()), (before + 1, []), "в бот — только сам вход")

    def test_vpn_key_and_its_qr_conf_download_the_file(self):
        b = self.b
        # awgNN.vpn — сам ключ vpn:// и его QR от моста (AmneziaVPN по ссылке не обновляется), адреса нет
        self.confirmed_tab("awg20.vpn", "198.51.100.43")
        self.press("document.getElementById('sc-key')")
        self.assertEqual(self.text("document.getElementById('sc-key')"), "vpn://FAKE-anna-awg2")
        self.assertEqual(self.text("document.querySelector('#sc-full [data-copy=\"sc-key\"]')"), "Скопировать ключ")
        self.assertFalse(b.js("!!document.getElementById('sc-url')"), "у ключа адреса нет")
        self.assertNotIn("/s-", b.js("document.documentElement.outerHTML"))
        self.assertTrue(self.images_loaded(2), "QR ключа — две страницы, обе загружены")
        self.assertEqual(b.js("[].map.call(document.querySelectorAll('#sc-full .pg-qr__cap'), function (c) { "
                              "return c.textContent; })"), ["QR ключа — 1 из 2", "QR ключа — 2 из 2"])
        self.assertFalse(b.js("document.getElementById('sc-content').hidden"))
        # .conf — адрес (роутеру и WG Tunnel), «Скачать файл» — тот же короткий адрес с меткой, команда Keenetic;
        # «Показать содержимое» у файла не нужна — её место заняла «Скачать файл»
        display, rid = self.confirmed_tab("awg20.conf", "198.51.100.44")
        self.press("document.getElementById('sc-url')")
        url = self.url("awg20.conf")
        self.assertEqual(self.text("document.getElementById('sc-url')"), url)
        self.assertEqual(b.js("[].map.call(document.querySelectorAll('#sc-full a.pg-mini'), function (a) { "
                              "return [a.getAttribute('href'), a.textContent]; })"),
                         [["/%s?go=%s" % (display, rid), "Скачать файл"]])
        self.assertTrue(b.js("document.getElementById('sc-content').hidden"))
        self.assertEqual(self.text("document.getElementById('sc-cmd-1')"), "interface Wireguard9 wireguard import " + url)
        if self.stand.application.qr.available():
            self.assertTrue(self.images_loaded(1))
        self.assertEqual(self.marks, [], "файл — только по нажатию")

    def test_confirm_states_fit_360_to_1440_in_both_themes(self):
        b, short = self.b, self.stand.short
        addr = "198.51.100.45"
        self.ip(addr)
        tabs = []
        for suffix in ("all.yaml", "awg20.conf", "awg20.vpn"):
            display = short.ensure("anna", suffix)["display"]
            tabs.append((suffix, display) + self.confirm_page(display))
        bad, qr = [], int(self.stand.application.qr.available())

        def measure(where):
            b.settle()
            return ["%d px %s, %s: %s" % (width, theme, where, m) for m in b.js(MEASURE_JS)]
        for theme in THEMES:
            for width in WIDTHS:
                b.viewport(width)
                b.media(theme)
                self.go("/" + tabs[0][1], "document.querySelector('#sc[data-state=\"pending\"]')")
                bad += measure("ожидание")
        for _suffix, _display, rid, number in tabs:
            short.decide(rid, number, "bot:111")
        notes = "[].forEach.call(document.querySelectorAll('[data-full]'), function (e) { e.removeAttribute('hidden'); })"
        for theme in THEMES:
            for width in WIDTHS:
                b.viewport(width)
                b.media(theme)
                self.ip(addr)
                for suffix, display, rid, _number in tabs:
                    self.go("/%s?r=%s&lang=ru" % (display, rid), self.READY)
                    if suffix == "all.yaml":
                        bad += measure("«Вход подтверждён»")
                        b.js(notes)              # загрузка и оба отказа разом — мерить и их
                        bad += measure("«Вход подтверждён», строки загрузки и отказов")
                    self.press()
                    self.assertTrue(self.images_loaded(2 if suffix.endswith(".vpn") else qr), suffix)
                    bad += measure("полная ссылка %s" % suffix)
                self.ip("198.51.100.46")
                self.go("/%s?go=%s" % (tabs[0][1], tabs[0][2]), "!document.querySelector('[data-sc=\"moved\"]').hidden")
                bad += measure("«Нужна новая проверка»")
        self.assertEqual(bad[:40], [], "%d нарушений" % len(bad))
        self.assertEqual(self.marks, [tabs[0][2]] * len(WIDTHS) * len(THEMES), "только переходы самого теста")
        self.assertEqual(sorted(set(self.asks)), sorted(t[2] for t in tabs))


def make_short_stand():
    """Стенд коротких ссылок как у tests/test_pages_server.py PageShortTests: бот настроен, подписка anna, три линии; у
    ключей vpn:// есть QR от моста (две страницы) — блок полной ссылки awgNN.vpn показывает их картинками."""
    class Stand(T.PageShortTests):
        def runTest(self):
            pass

        def world(self, data):
            png = png_b64()
            for keys in data.get("keys", {}).values():
                for key in keys.values():
                    key.setdefault("qr_vpn_png_b64", [png, png])
            return T.PageShortTests.world(self, data)
    stand = Stand("runTest")
    stand.setUp()
    return stand


def make_stand():
    """Стенд как у tests/test_pages_server.py (подписка anna, три линии), но у ключей есть QR — у карточек ключей
    «Статического ключа» появляются кнопки QR. Класс — внутри функции: иначе unittest собрал бы с ним и все проверки
    PageSubBase ещё раз."""
    class Stand(T.PageSubBase):
        def runTest(self):
            pass

        def world(self, data):
            png = png_b64()
            for keys in data.get("keys", {}).values():
                for key in keys.values():
                    key.setdefault("qr_conf_png_b64", png)
                    key.setdefault("qr_vpn_png_b64", [png, png])
            return T.PageSubBase.world(self, data)
    stand = Stand("runTest")
    stand.setUp()
    return stand


if __name__ == "__main__":
    unittest.main()
