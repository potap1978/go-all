"""BC4/BC5 (k3/handoff/SPEC-browser-channel.md §6.1–6.6, §8.1): расчёт, движок и карточка замера из браузера — офлайн в node.

tests/panel/test_speed_calc.mjs — чистые функции static/speed-calc.js; tests/panel/test_speed_worker.mjs — движок
static/speed-worker.js на подменённых часах и сервере; tests/panel/test_speed_card.mjs — чистые помощники карточки
static/pages/speed.js (BC5). Без node — пропуск."""
import os
import re
import shutil
import subprocess
import unittest

from tests import jsparts

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")


def run_node(name):
    r = subprocess.run(["node", os.path.join(HERE, name)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=300, cwd=ROOT)
    return r.returncode, r.stdout.decode("utf-8", "replace")


@unittest.skipUnless(shutil.which("node"), "нет node: расчёт замера из браузера не проверен")
class SpeedJsTest(unittest.TestCase):
    def test_calc(self):
        rc, out = run_node("test_speed_calc.mjs")
        self.assertEqual(rc, 0, out[-3000:])
        self.assertRegex(out, r"SPEED CALC OK: \d+ проверок в 19 тестах")

    def test_worker(self):
        rc, out = run_node("test_speed_worker.mjs")
        self.assertEqual(rc, 0, out[-3000:])
        self.assertRegex(out, r"SPEED WORKER OK: \d+ проверок в 20 тестах")

    def test_card(self):
        rc, out = run_node("test_speed_card.mjs")
        self.assertEqual(rc, 0, out[-3000:])
        self.assertRegex(out, r"SPEED CARD OK: \d+ проверок в 8 тестах")


class SpeedJsFilesTest(unittest.TestCase):
    """BLOCKS.md: ядро без DOM и без Kaskad, JS без сборщика и внешних пакетов; воркер грузит только своё ядро."""

    def read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def test_no_bundler_no_packages_no_dom(self):
        for name in ("speed-calc.js", "speed-worker.js"):
            code = re.sub(r"//[^\n]*", "", self.read(name))
            for bad in (r"\bimport\b", r"\brequire\s*\(", r"\bdocument\b", r"\bwindow\b", r"localStorage", r"https?://"):
                self.assertIsNone(re.search(bad, code), "%s: %s" % (name, bad))
        calc = re.sub(r"//[^\n]*", "", self.read("speed-calc.js"))
        self.assertIsNone(re.search(r"kaskad|fetch\s*\(|XMLHttpRequest|/api/", calc, re.I))
        worker = self.read("speed-worker.js")
        self.assertEqual(re.findall(r'importScripts\("([^"]+)"', worker), ["speed-calc.js"])
        # версия расчёта — та же, что у воркера: только ?v=<версия> его собственного адреса (без чужих частей адреса)
        self.assertIn(r'/^\?v=[\w.%-]{1,64}$/.test(root.location.search)', worker)
        # маршруты — только замера, под путём панели
        self.assertEqual(sorted(set(re.findall(r'"api/host/speed/"', worker))), ['"api/host/speed/"'])
        self.assertNotIn("/api/", worker.replace("/api/host/speed/*", ""))


class SpeedCardFilesTest(unittest.TestCase):
    """BC5: карточка — часть "speed" (K.loadPart): сеть — только воркер и K.API.host.speed.budget; атрибута style нет
    (CSP style-src 'self'); стили — speed.css после check.css; демо-воркер — только в ?mock=1."""

    def read(self, name):
        with open(os.path.join(STATIC, name), encoding="utf-8") as f:
            return f.read()

    def code(self, name):
        js = re.sub(r"/\*.*?\*/", "", self.read(name), flags=re.S)
        return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", js)

    def test_the_card_talks_only_through_the_worker_and_the_budget(self):
        js = self.code("pages/speed.js")
        for bad in ("fetch(", "XMLHttpRequest", "EventSource", "/api/", "request(", "`", "innerHTML", "eval(", "Function("):
            self.assertNotIn(bad, js, bad)
        self.assertEqual(re.findall(r"api\.(\w+)\(\)", js), ["budget"])
        self.assertIn('new Wk("speed-worker.js" + (K.ver ? "?v=" + encodeURIComponent(K.ver) : ""))', js)
        # демо-воркер — только когда панель сама в демо (?mock=1)
        self.assertIn("const mock = () => (K.mock() && window.KASKAD_SPEED_MOCK) || null;", js)
        self.assertNotIn("KASKAD_SPEED_MOCK", js.replace("const mock = () => (K.mock() && window.KASKAD_SPEED_MOCK) || null;", ""))

    def test_no_style_attribute(self):
        js = self.code("pages/speed.js")
        self.assertIsNone(re.search(r'setAttribute\(\s*"style"|\.style\s*=|style:\s*\{|cssText', js))
        self.assertEqual(re.findall(r"\.style\.setProperty\(\s*\"([^\"]+)\"", js), [])     # график — сетка KaskadSpark.grid (CSSOM там)
        css = self.read("speed.css")
        self.assertNotIn("@import", css)
        idx = self.read("index.html")
        self.assertLess(idx.index('href="check.css'), idx.index('href="speed.css?v=__KASKAD_V__"'))

    def test_registered_and_loaded(self):
        app = self.read("app.js")
        self.assertIn('const PART_NAMES = ["exits-groups", "speed", "ovb", "addresses"];', app)          # ovb — «Обзор» варианта Б (круг 1 Т1)
        self.assertIn('speed: ["speed-calc.js"]', app)
        self.assertIn('if (MOCK) { try { await loadScript("dev-mock-speed.js"); }', app)
        self.assertIn("K.part(ID, { build: (box) => box.replaceChildren(build()), card: build, pure: PURE });", self.read("pages/speed.js"))
        diag = self.code("pages/diag.js")
        self.assertIn('K.loadPart("speed")', diag)
        self.assertEqual(diag.count(", speedCard())"), 2)                  # и в загрузке/ошибке, и под карточкой проверки
        # перерисовка раздела переносит узел карточки — фокус внутри неё возвращается тому же элементу
        draw = jsparts.function(self.read("pages/diag.js"), "draw")
        self.assertIn("const keep = spdNode && spdNode.contains(document.activeElement) ? document.activeElement : null;", draw)
        self.assertIn("keep.focus({ preventScroll: true })", draw)


if __name__ == "__main__":
    unittest.main()
