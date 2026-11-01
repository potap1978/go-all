"""3.3.3 №38, R-333-qr «≤2 частей»: the window of issuing laid out in a real browser (tools/k3_give_layout.mjs,
headless Chrome over CDP, the panel's ?mock=1 and the wizard's step 6). The frame of the key's QR is never wider than
its block and never under another node (on a phone the wizard's margins, №7);
the columns of the panel and /p/ are side by side only when both fit, else one under the other in full width (№2);
a code under the limit of its real frame is folded «для этого экрана» (№1, №5).

Repeat «≤2 частей» (b2e3333): the wizard has no video column since 3.3.3 (a link «▶ Видео — скоро» in its bar) — the
step takes the whole column and the QR is shown at 4 px on 1100–1440, not folded (№1); a fold on a narrow computer window says «Расширьте окно…», not «the camera…» (№3).
Nothing scrolls the window by itself — not on opening, not on another platform, protocol or app: the choice and step 1
stay in view, «Часть N из M» stands above the code and is in view on 1366×657 and 1280×689; a code that does not fit
whole gets «Показать QR целиком», and only that button scrolls (№2).

No Chrome or node — skipped and said so; the CSS part is checked without them."""

import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, read

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_give_layout.mjs")
SCREEN = "QR слишком плотный для этого экрана"
NARROW = "В таком узком окне код получился бы слишком мелким для камеры. Расширьте окно"
SHOWN = ("panel-short-whole", "wizard-short-whole", "wizard-anyway", "wizard-phone-anyway")   # after a press of a button


def chrome_path():
    for p in (os.environ.get("K3_CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser")):
        if p and os.path.exists(p):
            return p
    return None


class ColumnsCssTest(unittest.TestCase):
    def test_the_columns_stack_until_both_fit(self):
        """№2: the bases of the columns and the gap — the width under which they go one under the other."""
        css = read("give.css")
        left = int(re.search(r"\n\.gs-col--l \{ flex: 1 1 (\d+)px;", css).group(1))
        right = int(re.search(r"\n\.gs-col--r \{ flex: 0 1 (\d+)px;", css).group(1))
        gap = int(re.search(r"\n\.gs-cols \{[^}]*?(?<![-\w])gap: (\d+)px;", css).group(1))
        stack = re.search(r"@container \(max-width: (\d+)px\) \{ \.gs-col--l, \.gs-col--r \{ flex: 1 1 100%; \} \}", css)
        self.assertIsNotNone(stack)
        self.assertEqual(int(stack.group(1)), left + gap + right - 1)


class ChromeServer(http.server.ThreadingHTTPServer):
    """The static of the panel for a headless Chrome. socketserver keeps 5 connections waiting to be accepted
    (request_queue_size); a page asks for ~20 files at once, and under load (k3_test -j4, two Chromes of
    test_335_hold) the accept thread lags: the queue overflows and Chrome gets no answer — the scripts of the page do
    not load (status 0), the page stays empty and the check says «не дождался» (REV-336-int П-3, PAGE_DIAG of
    k3_hold_check). The queue of node's own servers (k3_layout_check, k3_find_check) is 511."""
    request_queue_size = 128
    daemon_threads = True


class _Static(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):                       # /static/<path> — the same files (as server.py)
        if path.startswith("/static/"):
            path = path[len("/static"):]
        return super().translate_path(path)

    def log_message(self, *a):
        pass


class ChromeServerTest(unittest.TestCase):
    """R-s1-flaky: «не дождался» и «cut» у rail.welcomeSub под -j4/-j6 — Chrome не получил файл страницы (скрипт,
    шрифт): очередь 5 у ThreadingHTTPServer переполнялась, и macOS сбрасывала соединение (ECONNRESET), а не ждала."""

    def test_a_lagging_accept_loses_nothing(self):
        import socket
        import time

        class Lag(ChromeServer):
            def get_request(self):                         # поток приёма отстаёт, как под нагрузкой
                time.sleep(0.02)
                return super().get_request()

        class Ok(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"ok")

            def log_message(self, *a):
                pass
        srv = Lag(("127.0.0.1", 0), Ok)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        bad = []

        def one():
            try:
                with socket.create_connection(srv.server_address, timeout=10) as s:
                    s.sendall(b"GET / HTTP/1.0\r\n\r\n")
                    if b" 200 " not in s.recv(100):
                        bad.append("no 200")
            except OSError as e:
                bad.append(type(e).__name__)
        try:
            ts = [threading.Thread(target=one) for _ in range(40)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(bad, [], "40 соединений разом при отстающем приёме")

    def test_every_chrome_static_uses_it(self):
        """Статика на Python для Chrome — только ChromeServer; порт отладки Chrome выбирает сам (0), не случайный."""
        for name in ("test_333q_layout.py", "test_335_hold.py", "test_qs333_layout.py"):
            with open(os.path.join(ROOT, "tests", name), encoding="utf-8") as f:
                src = f.read()
            self.assertIsNone(re.search(r"ThreadingHTTPServer\(\(", src), name)
            self.assertIsNotNone(re.search(r"ChromeServer\(\(", src), name)
        tools = os.path.join(ROOT, "tools")
        for name in sorted(os.listdir(tools)) + ["../tests/setup/layout_scan.mjs"]:
            if name.endswith(".mjs"):
                with open(os.path.join(tools, name), encoding="utf-8") as f:
                    src = f.read()
                if "--remote-debugging-port" in src:
                    self.assertEqual(re.findall(r"--remote-debugging-port=[^\"]*", src), ["--remote-debugging-port=0"], name)


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: раскладка окна выдачи не проверена (K3_CHROME=…)")
class LayoutTest(unittest.TestCase):
    rows = None

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=200)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_give_layout: %s" % p.stderr.decode("utf-8", "replace")[-800:])
        cls.rows = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])

    def tag(self, r):
        return "%s %dx%d" % (r["where"], r["vw"], r["vh"])

    def test_every_place_and_width_was_seen(self):
        seen = {(r["where"], r["vw"]) for r in self.rows}
        for w in (1000, 1024, 1080, 1100, 1150, 1240, 1300):
            self.assertIn(("panel-open", w), seen)
        for w in (800, 1024, 1100, 1150, 1200, 1240, 1280, 1300, 1440, 1441, 1600):
            self.assertIn(("wizard-open", w), seen)
        for w in (390, 360):
            self.assertIn(("wizard-phone-anyway", w), seen)
        for where in ("wizard-anyway", "wizard-squeezed", "wizard-short", "wizard-short-whole", "panel-short-whole"):
            self.assertTrue(any(r["where"] == where for r in self.rows), where)
        self.assertEqual(len([r for r in self.rows if r["where"] == "panel-short-chip"]), 8)

    def test_the_frame_stays_in_its_block_and_is_not_covered(self):
        """№1, №7: min(side by the limit, width of the block); nothing lies over a corner of the code."""
        for r in self.rows:
            self.assertEqual(r.get("keyOut", 0), 0, self.tag(r))               # the buttons too (1240: 49 px out)
            if r.get("frame"):
                self.assertEqual(r["frameOut"], 0, self.tag(r))
                self.assertEqual(r["covered"], 0, self.tag(r))
                self.assertLessEqual(r["frame"]["w"], r["keyInner"], self.tag(r))

    def test_a_shown_code_is_above_the_limit_of_its_real_frame(self):
        """№1: the limit is counted by the frame as it stands; shown without «Всё равно показать» — 4 px (3.6 phone)."""
        for r in self.rows:
            if r.get("frame") and "anyway" not in r["where"]:
                self.assertGreaterEqual(r["modulePx"], (3.6 if r["phone"] else 4) - 0.01, self.tag(r))
            if r["dense"]:
                self.assertEqual(r["denseT"], SCREEN, self.tag(r))     # the screen is narrow, not the app (№5)

    def test_the_wizard_shows_the_code_on_a_laptop(self):
        """Repeat №1: no video column beside the step — the code is shown at 4 px in a 404 px frame, not folded."""
        rows = [r for r in self.rows if r["where"] in ("wizard-open", "wizard-resize", "wizard-short") and not r["phone"]]
        self.assertGreaterEqual(len(rows), 16)
        for r in rows:
            self.assertFalse(r["dense"], self.tag(r))
            self.assertIsNotNone(r.get("frame"), self.tag(r))
            self.assertGreaterEqual(r["modulePx"], 4 - 0.01, self.tag(r))
            self.assertGreaterEqual(r["frame"]["w"], 404, self.tag(r))

    def test_a_fold_on_a_narrow_window_says_widen_it(self):
        """Repeat №1, №3: a computer, the block too narrow — the width, not the camera; and a way to the phone."""
        rows = [r for r in self.rows if r["where"] == "wizard-squeezed"]
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertTrue(r["dense"], self.tag(r))
            self.assertTrue((r["denseX"] or "").startswith(NARROW), self.tag(r))
            self.assertIn("перешлите ключ себе", r["denseX"], self.tag(r))
            self.assertNotIn("Камера телефона", r["denseX"], self.tag(r))

    def test_columns_side_by_side_only_when_both_fit(self):
        for r in self.rows:
            if r.get("cols"):
                self.assertFalse(r["wrapBad"], self.tag(r))
                if r["where"].startswith("panel"):
                    self.assertEqual(r["cols"], "side" if r["gsW"] >= 988 else "stack", self.tag(r))

    def test_nothing_scrolls_the_window_by_itself(self):
        """Repeat №2: opening, another platform, protocol or app — the window stays where it was, the choice in view."""
        for r in self.rows:
            if r["where"] in ("panel-open", "panel-resize", "panel-short", "panel-short-chip", "panel-short-app"):
                self.assertEqual(r["scr"], 0, self.tag(r) + " " + str(r.get("chip")))
                self.assertTrue(r["selSeen"], self.tag(r))
            if r["where"] in ("wizard-open", "wizard-short"):
                self.assertEqual(r["scr"], r["scr0"], self.tag(r))                # «Выдать» does not jump to the QR

    def test_the_parts_are_above_the_code_and_in_view_on_a_laptop(self):
        """Repeat №2: «Часть N из M» above the code; on 1366×657 and 1280×689 it and the top of the code are in view;
        the code does not fit whole — «Показать QR целиком» is in view, and after it the code is whole."""
        for r in self.rows:
            if r.get("frame") and r.get("parts"):
                self.assertTrue(r["partsAbove"], self.tag(r))
        short = [r for r in self.rows if r["where"] in ("panel-short", "panel-short-chip", "panel-short-app")]
        self.assertEqual(len(short), 12)
        for r in short:
            self.assertTrue(r.get("frame"), self.tag(r))                          # 2 × v19 shown at 4 px, not folded
            self.assertRegex(r["parts"] or "", r"Часть [12] из 2", self.tag(r))
            self.assertTrue(r["partsSeen"] and r["frameTop"], self.tag(r))
            self.assertTrue(r["seen"] or r["whole"], self.tag(r))
        for r in self.rows:                                                       # a cut code always has the button
            if r.get("frame") and r["frameTop"] and not r["seen"] and not r["phone"]:
                self.assertTrue(r["whole"], self.tag(r))

    def test_the_code_is_whole_after_a_press(self):
        """«Показать QR целиком», «Всё равно показать», «QR для другого устройства» — the QR and its parts whole."""
        rows = [r for r in self.rows if r["where"] in SHOWN]
        self.assertGreaterEqual(len(rows), 8)
        for r in rows:
            self.assertTrue(r.get("frame"), self.tag(r))
            self.assertTrue(r["seen"], self.tag(r))
            self.assertRegex(r["parts"] or "", r"Часть [12] из 2", self.tag(r))
            self.assertFalse(r["whole"], self.tag(r))                             # the code is whole: the button is out
        back = [r for r in self.rows if r["where"] == "panel-short-back"]           # scrolled back by hand: cut, the button again
        self.assertEqual(len(back), 2)
        for r in back:
            self.assertEqual(r["scr"], 0, self.tag(r))
            self.assertTrue(r["whole"] and not r["seen"], self.tag(r))

    def test_the_button_follows_the_height_of_the_window(self):
        """Third round №1: the window grew (the code whole) — the button is out; it got lower (the code cut) — the
        button is in view; as well for a window opened tall and made lower."""
        rows = [r for r in self.rows if r["where"] in ("panel-taller", "panel-tall", "panel-lower")]
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertTrue(r.get("frame"), self.tag(r))
            if r["where"] == "panel-lower":
                self.assertTrue(r["whole"] and not r["seen"], self.tag(r))
            else:
                self.assertTrue(r["seen"] and not r["whole"], self.tag(r))

    def test_a_render_does_not_move_the_code(self):
        """Third round №3, №4: after «Показать QR целиком» another app and back — the dialog keeps its scroll, the code
        stays where it was, the button keeps its place (put out, not removed) at once after the render."""
        rows = [r for r in self.rows if r["where"] == "panel-short-rerender"]
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r["sync"], {"hidden": False, "off": True, "top": r["top0"]}, self.tag(r))
            self.assertEqual(r["scr"], r["scr0"], self.tag(r))
            self.assertEqual(r["frame"]["t"], r["top0"], self.tag(r))
            self.assertTrue(r["seen"] and not r["whole"], self.tag(r))

    def test_a_keyboard_press_leaves_the_focus_in_view(self):
        """Fourth round №1: Enter on «Показать QR целиком» — the button goes out, the focus is on a node in view (not
        on «Скопировать ключ» below the edge of the dialog) and has its ring. Nit 4: another app at once after it
        (within 1.2 s, after the wheel took the window back) does not scroll the window again — only an explicit press does."""
        rows = [r for r in self.rows if r["where"] in ("panel-kbd-whole", "wizard-kbd-whole")]
        self.assertEqual(len(rows), 3)
        for r in rows:
            f = r["focus"]
            self.assertFalse(f.get("none"), self.tag(r))
            self.assertTrue(f["seen"], self.tag(r) + " " + str(f))
            self.assertTrue(f["ring"], self.tag(r) + " " + str(f))
            self.assertLess(r["scrBack"], f["scr"], self.tag(r))                  # the wheel took the window back
            self.assertEqual(r["scrApp"], r["scrBack"], self.tag(r))
            self.assertTrue(r["whole"] and not r["seen"], self.tag(r))             # cut again: the button is back

    def test_reissue_in_the_wizard_keeps_the_button_in_place(self):
        """Third round №2 (3.3.2 №7): «Выдать» for another platform — no video column comes back and the step keeps its
        width while the key is issued; the button stands where it stood before, during and after."""
        rows = [r for r in self.rows if r["where"] == "wizard-reissue"]
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertTrue(r["during"]["busy"], self.tag(r))                    # measured while the key is issued
            for k in ("before", "during", "after"):
                self.assertFalse(r[k]["video"], self.tag(r) + " " + k)
                self.assertIsNotNone(r[k]["top"], self.tag(r) + " " + k)
                self.assertEqual(r[k]["mainW"], r["before"]["mainW"], self.tag(r) + " " + k)
            self.assertLessEqual(abs(r["during"]["top"] - r["before"]["top"]), 1, self.tag(r))
            self.assertLessEqual(abs(r["after"]["top"] - r["before"]["top"]), 1, self.tag(r))


if __name__ == "__main__":
    unittest.main()
