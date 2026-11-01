"""Круг 1 правок по видео владельца (27.10), пакет Л1 «логика и тексты» — проверки сервера панели.

* №31г: российский выход — строка GET /api/gw/exits (role «ru», имя ключа), а не id «e-…» в «Российских группах»;
  обзор и GET /api/gw по-прежнему считают только зарубежные выходы; имена CLI групп — тоже с российскими.
* №39: PUT /api/gw/host {to_ip: true} — «Вернуть на IP»: внешний IP узла вместо домена; частный адрес — отказ.
* №8: у пустого звена «Сейчас» (GET /api/host/score) — причина: why и why_text."""

from unittest import mock

from kaskad import state
from kaskad.gw import exits, model, net
from tests.fakes_gw import VLESS_B, write_ru_pool
from tests.panel.panelcase import PanelCase


class RussianExitRowsTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()
        write_ru_pool([VLESS_B])
        self.rid = exits.exit_id(VLESS_B)

    def test_the_russian_exit_is_a_named_row(self):
        rows = dict((r["id"], r) for r in self.ok("GET", "/api/gw/exits")["exits"])
        self.assertIn(self.rid, rows)                                   # без правки: только зарубежные — строки нет
        self.assertEqual(rows[self.rid]["role"], "ru")
        self.assertEqual(rows[self.rid]["name"], "Riga")                # имя ключа, не id
        self.assertEqual(sorted(r["role"] for r in rows.values()), ["abroad", "abroad", "ru"])

    def test_the_counts_stay_foreign(self):
        doc = self.ok("GET", "/api/overview")
        self.assertEqual(doc["exits"]["total"], 2)
        self.assertNotIn(self.rid, [x["id"] for x in doc["exits"]["items"]])
        self.assertNotIn(self.rid, [r["id"] for r in self.ok("GET", "/api/gw")["exits"]])

    def test_cli_names_of_the_russian_exit(self):
        from kaskad.gw import cli_policy
        names = cli_policy._exit_names(state.load())
        self.assertEqual(names.get(self.rid), "Riga")


class HostBackToIpTest(PanelCase):
    def setUp(self):
        super().setUp()
        self.gateway()

    def test_back_to_the_ip(self):
        self.assertEqual(self.ok("PUT", "/api/gw/host", {"host": "vpn.example.org"})["host"], "vpn.example.org")
        # адреса документации (203.0.113.0/24) — не is_global: внешним адрес маршрута делает подмена detect_host
        with mock.patch.object(net, "detect_host", return_value=("203.0.113.10", "route")):
            doc = self.ok("PUT", "/api/gw/host", {"to_ip": True})
        self.assertEqual((doc["host"], doc["auto"]), ("203.0.113.10", True))
        self.assertEqual(model.public_host(state.load()), "203.0.113.10")

    def test_a_private_address_is_refused(self):
        self.ok("PUT", "/api/gw/host", {"host": "vpn.example.org"})
        self.w.ip.links["eth0"]["addrs"][0] = "10.0.0.5/24"           # NAT: маршрут — частный адрес
        self.w.curl.echo_ip = None                                     # и эхо IP не ответило
        doc = self.err(409, "PUT", "/api/gw/host", {"to_ip": True})   # без правки: 400 «задайте адрес»
        self.assertIn("10.0.0.5", doc["error"])
        self.assertEqual(model.public_host(state.load()), "vpn.example.org")    # домен остался
        # «Определить» карточки «Шлюз» — как раньше: адрес маршрута, даже частный
        self.assertEqual(self.ok("PUT", "/api/gw/host", {"detect": True})["host"], "10.0.0.5")


from tests.panel.test_panel_score import S4, ScoreCase      # noqa: E402 — the score base of the panel tests


class LinkWhyTest(ScoreCase):
    """№8: у звена без свежего значения — причина (why, why_text), панель пишет её вместо «—»."""

    def links(self):
        doc = self.score()
        out = {}
        for lk in doc["links"]:
            out[lk["id"]] = lk
            for p in lk.get("parts") or ():
                out[lk["id"] + "." + p["test"]] = p
        return out

    def test_no_checks_and_no_probes(self):
        ls = self.links()
        self.assertEqual((ls["down"]["why"], ls["down"]["why_text"]), ("never", "Ещё не измерялось: проверок сервера пока не было"))
        self.assertEqual(ls["net_q.rtt"]["why"], "probes")                  # отклик — фоновые пробы, не проверка
        self.assertTrue(ls["net_q.rtt"]["why_text"].startswith("Не измерено: фоновые пробы"))

    def test_upload_not_measured_in_russia(self):
        vals = dict(S4)
        vals["net_up"] = {"v": None, "ok": False, "why": "no_upload_ru"}
        self.check(3600, vals)
        ls = self.links()
        self.assertIsNotNone(ls["down"]["fresh_raw"])
        self.assertEqual((ls["down"]["why"], ls["down"]["why_text"]), (None, None))     # измерено — причины нет
        self.assertEqual(ls["up"]["fresh_raw"], None)
        # Л3 №8: код раннера, не общее «failed» («ошибка замера» на узле в России, где отдача просто не меряется)
        self.assertEqual((ls["up"]["why"], ls["up"]["why_text"]),
                         ("no_upload_ru", "Не измерено: сервер в России, а точки для замера отдачи нет"))

    def test_the_reasons_of_the_runner_pass_as_codes(self):
        """Л3 №8: excluded / no_target / no_iperf3 — своим кодом (панель: «выключено в настройках», «нет точки
        замера»); причина раннера без своего текста или неизвестная — по-прежнему «failed»."""
        from kaskad.bench import hostscore as HS
        for k, why in enumerate(("excluded", "no_target", "no_iperf3", "tool_failed", "evil")):
            vals = dict(S4)
            vals["net_down"] = {"v": None, "ok": False, "why": why}
            self.check(3600 - 60 * k, vals)                                # каждая следующая — новее
            got = self.links()["down"]
            want = why if why in HS.RUN_WHY else "failed"
            self.assertEqual(got["why"], want, why)
            self.assertTrue(got["why_text"], why)

    def test_the_synthesis_says_why_too(self):
        """Л3 №8: окно «Балл сервера» и подпись радара (GET /api/host/synth) — та же причина у звена без балла."""
        vals = dict(S4)
        vals["net_up"] = {"v": None, "ok": False, "why": "no_upload_ru"}
        self.check(3600, vals)
        links = dict((x["id"], x) for x in self.ok("GET", "/api/host/synth")["links"])
        self.assertIsNone(links["up"]["fresh"])
        self.assertEqual((links["up"]["why"], links["up"]["why_text"]),
                         ("no_upload_ru", "Не измерено: сервер в России, а точки для замера отдачи нет"))
        self.assertIsNotNone(links["down"]["fresh"])
        self.assertEqual((links["down"]["why"], links["down"]["why_text"]), (None, None))

    def test_not_in_the_checks(self):
        vals = dict(S4)
        del vals["net_up"]
        self.check(3600, vals)
        self.assertEqual(self.links()["up"]["why"], "not_run")

    def test_the_why_of_a_whole_check_is_not_shown(self):
        vals = dict(S4)
        vals["net_up"] = {"v": None, "ok": False, "why": "busy"}           # текст с {mbps} — не строки теста
        self.check(3600, vals)
        self.assertEqual(self.links()["up"]["why_text"], "Не измерено: замер в последней проверке не удался")

    def test_the_why_of_a_whole_check_in_the_strict_mode(self):
        # слияние круга 1: строгий tr() (K3_TR_STRICT=1 в k3_test.sh) — текст с {mbps} не зовётся без чисел (было 500)
        import os
        vals = dict(S4)
        vals["net_up"] = {"v": None, "ok": False, "why": "busy"}
        self.check(3600, vals)
        with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1"}):
            self.assertEqual(self.links()["up"]["why_text"], "Не измерено: замер в последней проверке не удался")

    def test_every_code_has_both_languages(self):
        from kaskad.bench import hostscore as HS
        from kaskad.i18n import has
        for code in HS.WHY_CODES:
            key = ("bench_run.why." if code in HS.RUN_WHY else "score.link.why.") + code
            for lang in ("ru", "en"):
                self.assertTrue(has(key, lang), (code, lang))
