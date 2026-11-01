"""T-3377 (стенд 3.3.7.7), критерий С4 «ни одной ошибки в консоли»: обычный путь панели не получает ответов 4xx.

Браузер пишет в консоль любой ответ 4xx как ошибку, даже ожидаемый панелью. На стенде было два источника:

* Н-2 — «Люди» и вход спрашивали /api/gw/activity, а маршрута до 3.3.7.9 нет (404 на каждой загрузке). Теперь сервер
  говорит в /api/session (только вошедшему), что у него есть (``has.activity`` — по самим маршрутам), и панель не спрашивает
  того, чего нет: ресурс сразу «нет данных», как прежде по 404.
* Н-15 — окно «Проверить сервер» узнавало цену отказом POST /api/host/bench без ключа (409 need_confirm, дважды на
  открытие). Теперь цена — чтением GET /api/host/bench/price (200), запуск — POST с ключом, как прежде.

В настоящем Chrome (tools/k3_quiet_check.mjs, мок ?mock=1 с ответами сервера): «Люди» и вход у сервера без сбора
активности, окно цены → галочка программ → «Проверить»; 1440 и 390. Без node или Chrome — пропуск с причиной; правило
в коде проверяется и без них."""

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
CHECK = os.path.join(ROOT, "tools", "k3_quiet_check.mjs")


class RuleTest(unittest.TestCase):
    def test_the_panel_asks_only_what_the_server_has(self):
        js = read("app.js")
        self.assertIn('defRes("activity", API.activity.sum, normActivity, { optional: true, need: "activity" });', js)
        self.assertIn("if (r.need && isObj(S.has) && S.has[r.need] === false) {", js)
        self.assertIn("if (isObj(s) && isObj(s.has)) S.has = s.has;", js)
        mock = read("dev-mock.js")
        self.assertIn('has: authed() ? { activity: ACT !== "none" } : undefined', mock)

    def test_the_window_of_the_price_reads(self):
        js, diag = read("app.js"), read("pages/diag.js")
        self.assertIn('price: () => request("GET", "/api/host/bench/price"),', js)
        self.assertIn("const start = API().start, readPrice = API().price;", diag)
        self.assertNotIn("start({ mode: m })", diag)                    # no POST without a key any more
        self.assertIn("path === '/api/host/bench/price' && method === 'GET'", read("dev-mock-diag.js"))


@unittest.skipUnless(HAS_NODE and chrome_path(), "нет node или Chrome: консоль обычного пути не проверена (K3_CHROME=…)")
class ChromeTest(unittest.TestCase):
    rows = []

    @classmethod
    def setUpClass(cls):
        srv = ChromeServer(("127.0.0.1", 0), functools.partial(_Static, directory=STATIC))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            p = subprocess.run(["node", CHECK, "http://127.0.0.1:%d" % srv.server_address[1], chrome_path()],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=260)
        finally:
            srv.shutdown()
            srv.server_close()
        if p.returncode != 0:
            raise AssertionError("k3_quiet_check: %s" % p.stderr.decode("utf-8", "replace")[-800:])
        cls.rows = json.loads(p.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_every_place_opens(self):
        self.assertEqual(sorted((r["where"], r["w"]) for r in self.rows),
                         sorted((w, n) for w in ("people", "enter", "bench") for n in (1440, 390)))
        self.assertEqual([(r["where"], r["w"]) for r in self.rows if not r["ok"] or not r["n"]], [])

    def test_no_4xx_and_a_clean_console(self):
        for r in self.rows:
            self.assertEqual(r["bad"], [], (r["where"], r["w"]))
            self.assertEqual(r["errors"], [], (r["where"], r["w"]))

    def test_people_without_activity_say_no_data(self):
        for r in self.rows:
            if r["where"] == "people":
                self.assertEqual(r["steps"], ["activity:missing"], r["w"])   # as by 404: the lines «нет данных»

    def test_the_check_starts_with_one_post(self):
        for r in self.rows:
            if r["where"] == "bench":
                self.assertEqual(r["steps"], ["open:true", "tick:true", "go:true", "closed:true", "posts:200"], r["w"])
