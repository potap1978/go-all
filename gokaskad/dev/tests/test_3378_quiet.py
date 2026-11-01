"""T-3378 Н-2 / REV-3378-final С1 (стенд 3.3.7.8), критерий С4 «ни одной ошибки в консоли»: окна согласия и цены VLESS
не получают ответов 4xx.

Браузер пишет в консоль любой ответ 4xx как ошибку, даже ожидаемый панелью. Окна прежде узнавали вопрос отказом ядра:
``409 reality_consent`` (согласие на Reality) и ``409 vless_change`` (цена: «Перейти на TLS» Ц4, «Выключить сайт» Ц1,
«Включить сайт» Ц2 — в настройках и в карточке, «Убрать домен»). Теперь вопрос — чтением ``GET /api/gw/vless/ask``
(200 с ``need``), запись — прежний запрос сразу с ответом.

В настоящем Chrome (tools/k3_vless_quiet.mjs, мок ?mock=1 с ответами сервера) каждое окно открывается обычным путём и
закрывается отказом («Назад» / «Отмена»): ни одного ответа ≥ 400, консоль чистая, одно чтение 200, ни одного запроса
записи, режим VLESS и сайта у мока тот же; 1440 и 390. Без node или Chrome — пропуск с причиной; правило в коде
проверяется и без них."""

import functools
import json
import os
import subprocess
import threading
import unittest

from tests.test_332f_give import HAS_NODE, read
from tests.test_333q_layout import ChromeServer, _Static, chrome_path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
CHECK = os.path.join(ROOT, "tools", "k3_vless_quiet.mjs")
WHERE = ("consent", "tls", "card_on", "off", "on", "clear")


class RuleTest(unittest.TestCase):
    def test_every_window_reads_first(self):
        js = read("app.js")
        self.assertIn('ask: (q) => request("GET", "/api/gw/vless/ask" + qs(q)),', js)
        self.assertIn("let extra = {}, pre = o.ask ? await vlAsk(o.ask, o.busy) : null;", js)           # vlRun
        self.assertIn('ask: { do: "tls" },', js)                                                         # Ц4
        self.assertIn('ask: { do: "reality", site } });', js)                                            # согласие
        self.assertIn('ask: { do: "site_on" }, price: (dd) => vlSitePriceOpts(dd, "on") });', js)       # Ц2, карточка
        self.assertIn('const asked = await vlAsk({ do: on ? "site_on" : "site_off" }, busy);', js)       # Ц1, Ц2
        self.assertIn('let pre = vlPriceRead(await vlAsk({ do: "clear" }, t("site.clearBusy")));', js)   # «Убрать домен»
        self.assertIn('if (method === "GET" && path === "/api/gw/vless/ask") return vlAskDoc(query);', read("dev-mock.js"))


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: консоль окон VLESS не проверена (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    rows = []

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_vless_quiet: %s" % p.stderr.decode("utf-8", "replace")[-800:])
        cls.rows = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_every_window_opens_and_closes(self):
        self.assertEqual(sorted((r["where"], r["w"]) for r in self.rows), sorted((w, n) for w in WHERE for n in (1440, 390)))
        self.assertEqual([(r["where"], r["w"], r["steps"]) for r in self.rows if not r["ok"] or not r["n"]], [])
        for r in self.rows:
            for s in ("dialog:true", "price:true", "refuse:true", "closed:true"):
                self.assertIn(s, r["steps"], (r["where"], r["w"]))
            self.assertTrue([s for s in r["steps"] if s.startswith("title:") and len(s) > 6], (r["where"], r["w"]))
            # REV-it1-b З-1: строка VLESS раскрыта — кнопка «Маскировка…» одна (сцены, где строку раскрывали)
            for s in r["steps"]:
                if s.startswith("maskBtns:"):
                    self.assertEqual(s, "maskBtns:1", (r["where"], r["w"], r["steps"]))
        self.assertTrue(any(s.startswith("maskBtns:") for r in self.rows for s in r["steps"]))

    def test_no_4xx_and_a_clean_console(self):
        for r in self.rows:
            self.assertEqual(r["bad"], [], (r["where"], r["w"]))
            self.assertEqual(r["errors"], [], (r["where"], r["w"]))

    def test_one_read_and_nothing_written(self):
        for r in self.rows:
            self.assertEqual(r["asks"], [200], (r["where"], r["w"]))          # the question — by the read, 200
            self.assertEqual(r["writes"], [], (r["where"], r["w"]))           # refused: no request of the action
            self.assertTrue(r["before"], (r["where"], r["w"]))
            self.assertEqual(r["after"], r["before"], (r["where"], r["w"]))   # the mode of VLESS and of the site

    def test_back_returns_to_the_mask_window_and_clear_asks_once(self):
        for r in self.rows:
            if r["where"] == "consent":
                self.assertEqual(r["steps"][-3:], ["mask:true", "cancel:true", "maskClosed:true"], r["w"])
            if r["where"] == "clear":
                self.assertEqual(r["steps"][-1], "one:true", r["w"])           # one window (Н-10), no second question
