"""Kaskad 3.3.3 — the layout of the wizard «Быстрый старт» (owner, 2026-10-12, snapshot
k3/design/port-332/owner-2026-10-12-stage2.png):

    №1  the scheme «Classic Kaskad»: plaques by the width of their text with padding, the dashes only between them,
        all in the middle of the card; long English words stay inside;
    №2  the scheme «Protocol translator»: signed plaques — three devices on the left, two exits on the right; the
        lines meet at «node» and part from it without crossing any text;
    №3  the badge «recommended» never covers the name of a protocol: the name in one line, the badges in their own
        row, the same in every card;
    №4  the description of a protocol is whole — no «…»;
    №5  cards of one row have one height; the yellow lines of one row stand at one height;
    and the same kinds of faults on the other stages (the name of a file, an address with «Copy»).

The source checks run everywhere; the browser check (tests/setup/layout_scan.mjs, headless Chrome) measures the real
layout — getBoundingClientRect of the texts, the badges and the lines, scrollWidth of the texts — on the scenes of the
mock (?mock=1) in RU/EN, dark/light, 1440/390. It also looks for Cyrillic on the English screens (the progress of the
jobs included). Without node or Chrome it is SKIPPED."""

# k3-par: split=test — пять проходов Chrome по 2–8 мин, независимы: tools/k3_unit_par.py гоняет их рядом
import functools
import http.server
import json
import os
import re
import shutil
import subprocess
import threading
import unittest

from tests.test_333q_layout import ChromeServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
SCAN = os.path.join(ROOT, "tests", "setup", "layout_scan.mjs")
CHROMES = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/google-chrome", "/usr/bin/chromium",
           "/usr/bin/chromium-browser", "/snap/bin/chromium")
CHROME = next((p for p in ((os.environ.get("CHROME") or None),) + CHROMES if p and os.path.exists(p)), None)


@functools.lru_cache(maxsize=None)
def _node_major():
    try:
        out = subprocess.run(["node", "-v"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20).stdout
        return int(out.decode().strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def browser_missing():
    """Why the layout of the wizard is not checked in a real browser here ("" — it is): said by k3_test.sh as a line of
    its own (R-333 №3: on a VPS without Chrome the check went to skip silently)."""
    if not shutil.which("node"):
        return "пропущено: нет node (нужен 22+)"
    if _node_major() < 22:
        return "пропущено: node старше 22 (в нём нет WebSocket для CDP)"
    if not CHROME:
        return "пропущено: нет Chrome (apt install chromium или google-chrome; свой путь — CHROME=…)"
    return ""


def read(rel):
    with open(os.path.join(STATIC, rel), encoding="utf-8") as f:
        return f.read()


def block(js, head):
    i = js.index(head)
    depth, j = 0, js.index("{", i)
    while True:
        depth += {"{": 1, "}": -1}.get(js[j], 0)
        j += 1
        if depth == 0:
            return js[i:j]


class SourceTest(unittest.TestCase):
    def test_screen_widths_split_covers_all(self):
        """R-speed-2: проходы Chrome разбиты на части (быстрее рядом) — вместе это прежний набор: все ширины WIDTHS,
        у мастера UX-333 — обе темы на каждом языке."""
        self.assertEqual(sorted(WIDTHS_A.split(",") + WIDTHS_B.split(",")), sorted(WIDTHS.split(",")))
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            src = f.read()
        got = set(re.findall(r'self\.wizard_ux_333_stages\("(\w+)", "(\w+)"\)', src))
        self.assertEqual(got, {(lang, theme) for lang in ("ru", "en") for theme in ("dark", "light")})

    def test_schemes_are_html_plaques_with_lines_between(self):
        js = read("setup/setup.js")
        cl, nd = block(js, "function miniClassic()"), block(js, "function miniNode()")
        for src in (cl, nd):
            self.assertIn("qs-dia", src)
            self.assertNotIn('s("rect"', src)                 # no SVG boxes with a fixed width under the words
            self.assertNotIn('s("text"', src)
        self.assertEqual(cl.count("diaL()"), 2)                 # device - - this server - - other
        for k in ("mode.picPhone", "mode.picLaptop", "mode.picRouter", "mode.picExit", "mode.picNode"):
            self.assertIn(k, nd)
            self.assertIn('"%s"' % k, read("setup/setup-i18n-ru.js"))
            self.assertIn('"%s"' % k, read("setup/setup-i18n-en.js"))
        self.assertEqual(nd.count("diaFan("), 2)                # fans to «node» and from it, own elements
        css = read("setup/qs.css")
        # R-333 №2: the plaques shrink and wrap their words inside the card at any width; the lines go short first
        self.assertRegex(css, r"\.qs-dia__p \{[^}]*flex: 0 1 auto[^}]*white-space: normal")
        self.assertNotRegex(css, r"(^|\n)\.qs-dia__p \{[^}]*(flex: none|white-space: nowrap)")
        self.assertRegex(css, r"\.qs-dia__l \{[^}]*flex: 1 1 0[^}]*max-width: 44px")
        self.assertRegex(css, r"\.qs-dia \{[^}]*justify-content: center")
        self.assertRegex(css, r"\.qs-grid-modes \{[^}]*auto-fit")         # one column while two do not fit
        self.assertIn('"qs-grid-2 qs-grid-modes"', block(js, "function modeCards()"))

    def test_proto_card_name_alone_badges_in_their_row(self):
        js = read("setup/setup.js")
        view = block(js, "VIEW.protocols = () => {")
        row = re.search(r'h\("span", \{ class: "qs-choice__row" \}, box\(\), h\("span", \{ class: "qs-choice__t", '
                        r'text: p\.title \}\)\)', view)
        self.assertIsNotNone(row, "the row of the name holds the box and the name only")
        self.assertLess(view.index('"qs-choice__d"'), view.index('"qs-choice__tags"'))
        css = read("setup/qs.css")
        self.assertNotRegex(css, r"\.qs-choice--proto \.qs-choice__d \{[^}]*(nowrap|ellipsis)")
        self.assertRegex(css, r"\.qs-choice--proto \.qs-choice__t \{[^}]*white-space: nowrap")
        self.assertRegex(css, r"grid-template-rows: subgrid")    # one grid for the rows of the cards of a row
        for sel in (r"\.qs-file b", r"\.qs-code > code"):
            self.assertNotRegex(css, sel + r" \{[^}]*ellipsis")


@functools.lru_cache(maxsize=None)
def _server():
    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=STATIC, **k)

        def translate_path(self, path):
            if path.startswith("/static/"):
                path = path[len("/static"):]
            return super().translate_path(path)

        def log_message(self, *a):
            pass
    srv = ChromeServer(("127.0.0.1", 0), H)        # очередь соединений 128, не 5 (test_333q_layout.ChromeServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return "http://127.0.0.1:%d" % srv.server_address[1]


def scan(*args):
    r = subprocess.run(["node", SCAN, _server()] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=1500)
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    if not out:
        raise AssertionError("layout_scan.mjs: " + r.stderr.decode("utf-8", "replace")[-800:])
    return json.loads(out[-1])


def said(problems):
    return "\n".join("%s %s/%s/%s %s" % (p.get("id"), p.get("w"), p.get("theme"), p.get("lang"),
                                         json.dumps({k: v for k, v in p.items() if k not in ("id", "w", "theme", "lang")},
                                                    ensure_ascii=False)) for p in problems[:40])


# R-333 №2/№3: the widths of real screens — 1100–1260 a laptop with the video column beside (the schemes of the modes
# lay on each other there), 1024 the single column, 360 the narrow phone; 1440/390 in both themes above
# R-333 П1: 1240 — подсказка нижней панели ложилась на «Пропустить»; 3.3.3 П6: 320 — «Установить» и заголовки целиком
WIDTHS = "1024,1100,1180,1240,1260,1366,1920,360,320"
WIDTHS_A, WIDTHS_B = "1024,1100,1180,1240,1260", "1366,1920,360,320"     # R-speed-2: два теста, вместе — WIDTHS


@unittest.skipIf(browser_missing(), browser_missing())
class BrowserTest(unittest.TestCase):
    def test_stage2_every_language_theme_width(self):
        got = scan("--only", "protocols", "--cyr")
        if got.get("skip"):
            self.skipTest(got["skip"])
        self.assertGreaterEqual(got["scenes"], 80)
        self.assertEqual(got["problems"], [], said(got["problems"]))

    def stage2_screen_widths(self, widths):
        """R-speed-2: ширины WIDTHS двумя тестами (проход одним был ~5 мин — самый долгий в шаге юнит-тестов);
        вместе — все ширины WIDTHS (test_screen_widths_split_covers_all)."""
        got = scan("--only", "protocols", "--themes", "dark", "--widths", widths)
        if got.get("skip"):
            self.skipTest(got["skip"])
        self.assertGreaterEqual(got["scenes"], len(widths.split(",")) * 2 * 10)
        self.assertEqual(got["problems"], [], said(got["problems"]))

    def test_stage2_screen_widths(self):
        self.stage2_screen_widths(WIDTHS_A)

    def test_stage2_screen_widths_b(self):
        self.stage2_screen_widths(WIDTHS_B)

    def wizard_ux_333_stages(self, lang, theme):
        """3.3.3 (UX-333): DomainSteps, выходы двумя вариантами, «Откройте порты у хостера» — обе темы, RU/EN, ширины
        экранов; на английском — без кириллицы. По языку и теме отдельным тестом: проход длинный (~3,5 мин на язык и
        тему; R-speed-2: был по языку — ~7 мин), а tools/k3_unit_par.py гоняет тесты этого модуля рядом."""
        got = scan("--only", "domain|exits|done.summary|done.summaryDomain", "--widths", "1440,1240,1100,390,360", "--cyr",
                   "--langs", lang, "--themes", theme)
        if got.get("skip"):
            self.skipTest(got["skip"])
        self.assertGreaterEqual(got["scenes"], 25 * 5)
        self.assertEqual(got["problems"], [], said(got["problems"]))

    def test_wizard_ux_333_stages_ru(self):
        self.wizard_ux_333_stages("ru", "dark")

    def test_wizard_ux_333_stages_ru_light(self):
        self.wizard_ux_333_stages("ru", "light")

    def test_wizard_ux_333_stages_en(self):
        self.wizard_ux_333_stages("en", "dark")

    def test_wizard_ux_333_stages_en_light(self):
        self.wizard_ux_333_stages("en", "light")

    def test_walk_the_stages_in_a_row(self):
        """R-333i Б1: этапы подряд, как у человека (2 → 3 «Через выходы» → 4 → отказ выпуска → 5 → 3 → 6), без сброса
        между сценами: на странице нет исключений и console.error, внизу — кнопки своего этапа, после отказа выпуска —
        «Дальше без домена». Ошибки страницы — находка и во всех остальных проходах (k «js»)."""
        got = scan("--walk", "--widths", "1440,390,320", "--cyr")
        if got.get("skip"):
            self.skipTest(got["skip"])
        self.assertGreaterEqual(got["scenes"], 9 * 2 * 2 * 3)
        self.assertEqual(got["problems"], [], said(got["problems"]))

    def test_other_stages_found_by_the_scan(self):
        got = scan("--only", "import.file|import.backup|domain.ok|domain.ipgone|domain.sitedone|exits.adding|"
                   "import.progress", "--themes", "dark", "--cyr")
        if got.get("skip"):
            self.skipTest(got["skip"])
        self.assertGreaterEqual(got["scenes"], 20)
        self.assertEqual(got["problems"], [], said(got["problems"]))


if __name__ == "__main__":
    unittest.main()
