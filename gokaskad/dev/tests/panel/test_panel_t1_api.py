"""Круг 1, Т1 (вариант Б «Обзор»): данные панели для Сводки и «Проверки сервера».

- GET /api/host/synth — «сухая синтетика» 0–1000 восьми звеньев и итог (``hostscore.synth_doc``): только админ, только
  GET без параметров; слои «свежие» / «обычно» / разброс, ряд итогов по проверкам, автоповтор внутри своей проверки,
  пустая история; калибровка по звеньям (владелец 28.10, решение 5): ``links[].calib`` — «Процессор» и «Шифрование»
  выверены по эталонам К1, остальные — «предварительно», ``calibrated`` (вся шкала) false; самих эталонов (хостер,
  тариф, процессор, город) в ответе нет (владелец 29.10, D-142); первые часы
  нового узла (решение 4, вариант А) — ``provisional`` и итог без «Сети» и «Потерь».
- GET /api/gw/exits/hour — «Выходы · час»: 60 минут m1 всех выходов одним ответом (задержка, потери, трафик,
  состояние); ETag/304; чужой ввод (параметры) — 400.
- GET /api/host/health — у каждого ``checks[rid]`` пороги его правила (``warn_at``/``crit_at``/``down``).
"""

import json
import time
import unittest

from kaskad.bench import hostscore as HS
from kaskad.gw import hostmon, metrics
from kaskad.panel import api, routes
from tests.panel.test_panel_host import BOT, HostPanelCase
from tests.panel.test_panel_lines_live import E1, E2, ExitLineCase
from tests.panel.test_panel_score import DAY, S4, ScoreCase


class SynthTest(ScoreCase):
    def synth(self):
        return self.ok("GET", "/api/host/synth")

    def no_references(self, doc):
        """Владелец 29.10 (D-142): эталоны балла наружу не уходят — ни ``refs``, ни хостера, тарифа, процессора, города
        (``score_abs.REFS`` — только внутри, для шкалы)."""
        from kaskad import score_abs as SA
        self.assertNotIn("refs", doc)
        keys = set()

        def walk(x):
            if isinstance(x, dict):
                keys.update(x)
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(doc)
        self.assertFalse(keys & {"refs", "hoster", "tariff", "city", "dedicated", "vcpu"}, keys)
        text = json.dumps(doc, ensure_ascii=False).lower()
        for w in sorted({str(r[k]) for r in SA.REFS for k in ("hoster", "tariff", "cpu")} | {"9950x", "ryzen"}):
            self.assertNotIn(w.lower(), text, w)

    def test_login_get_only_and_no_parameters(self):
        r, _m = routes.find("GET", "/api/host/synth")
        self.assertEqual((r.auth, r.runs), ("admin", False))
        self.assertEqual(self.call("GET", "/api/host/synth", tunnel=False).status, 401)
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.call(method, "/api/host/synth", {}).status, 405, method)
        r = self.call("GET", "/api/host/synth?x=1")
        self.assertEqual(r.status, 400)
        self.assertEqual(r.json()["data"]["field"], "x")
        r = self.call("GET", "/api/host/synth?" + "y" * 200 + "=1")
        self.assertEqual((r.status, r.json()["data"]["field"]), (400, "query"))

    def test_empty_history(self):
        doc = self.synth()
        self.assertEqual((doc["ok"], doc["calibrated"], doc["version"], doc["anchors"], doc["margin"]),
                         (True, False, "abs-0.5-k1", [100, 500, 700, 900], 350))    # выверены не все звенья
        self.assertEqual([x["id"] for x in doc["links"] if x["calib"]], ["cpu", "crypto"])     # К1
        self.no_references(doc)
        self.assertIsNone(doc["provisional"])            # пробы на этом узле не идут (шлюз выключен)
        from kaskad import score as S
        self.assertEqual(doc["net_window_h"], S.WINDOW_HOURS_MIN)    # порог — правило кода, для «Как считается»
        self.assertEqual(doc["checks"], [])
        self.assertIsNone(doc["fresh"]["total"])
        self.assertEqual([x["id"] for x in doc["links"]],
                         ["net_bw", "net_q", "cpu", "stab", "down", "up", "loss", "crypto"])
        self.assertTrue(all(x["usual"] is None and x["lo"] is None for x in doc["links"]))
        self.assertEqual(doc["totals"], {"usual": None, "min": None, "max": None, "n": 0})

    def test_fresh_usual_band_and_the_checks(self):
        for d in range(12, 0, -1):                       # 12 полных проверок, отдача в последних трёх — втрое ниже
            self.check(d * DAY, dict(S4, net_up=430) if d <= 3 else S4)
        self.probes(hours=14 * 24)
        doc = self.synth()
        self.assertEqual(len(doc["checks"]), 12)
        self.assertEqual(doc["checks"][0]["ts"], max(c["ts"] for c in doc["checks"]))   # новые первыми
        up = next(x for x in doc["links"] if x["id"] == "up")
        self.assertEqual(up["n"], 9)                     # «обычно» — без 3 последних
        self.assertIsNotNone(up["usual"])
        self.assertLess(up["fresh"], up["usual"])        # свежие по 3 последним — хуже обычного
        self.assertIsNotNone(up["lo"])                   # 9 ≥ 8 — есть разброс
        self.assertLessEqual(up["lo"], up["hi"])
        self.assertIsNotNone(doc["fresh"]["total"])
        self.assertEqual(doc["totals"]["n"], 12)
        self.assertLessEqual(doc["totals"]["min"], doc["totals"]["max"])
        c = doc["checks"][0]
        self.assertEqual(set(c), {"id", "ts", "day", "mode", "by", "total", "raw", "cap", "weak", "limited", "why",
                                  "links"})
        self.assertIsNotNone(c["links"]["net_q"])        # пробы дня проверки дошли до звена «Сеть»
        self.no_references(doc)                          # и с историей — без эталонов (D-142)
        for x in doc["links"]:
            for k in ("fresh", "usual", "lo", "hi"):
                self.assertTrue(x[k] is None or (isinstance(x[k], int) and 0 <= x[k] <= 999), (x["id"], k, x[k]))

    def test_an_automatic_repeat_is_inside_its_check(self):
        first = self.check(2 * DAY, S4)
        self.check(2 * DAY - 900, dict(S4, net_down=1900), by="auto", retry_of=first)
        doc = self.synth()
        self.assertEqual([c["id"] for c in doc["checks"]], [first])

    def test_old_checks_and_failed_ones_are_left_out(self):
        self.check(70 * DAY, S4)                         # старше 60 дней
        self.check(DAY, {}, status="failed")
        self.assertEqual(self.synth()["checks"], [])


class SynthYoungTest(ScoreCase):
    """Владелец 28.10, решение 4 (вариант А R-it1-probe): первые 12–14 ч нового узла «Сеть» ещё копит лёгкие пробы —
    итог считается по остальным звеньям и помечен «предварительно»; порог — правило кода ``S.WINDOW_HOURS_MIN``."""

    def doc(self, probing=True):
        from kaskad import score as S
        return HS.synth_doc(S.load_spec(), now=self.now, probing=probing)

    def setUp(self):
        super().setUp()
        for d in (3, 2, 1):
            self.check(d * 3600, dict(S4, dns=150))      # три проверки за первые часы; DNS резолвера хостера — 150 мс

    def test_the_first_hours_total_without_the_network(self):
        from kaskad import score as S
        from kaskad import score_abs as SA
        self.probes(hours=S.WINDOW_HOURS_MIN - 1)        # 11 ч проб — окна ещё нет
        doc = self.doc()
        self.assertEqual(doc["provisional"], {"why": "net_young", "have_h": S.WINDOW_HOURS_MIN - 1,
                                              "need_h": S.WINDOW_HOURS_MIN})
        f = doc["fresh"]
        self.assertIsNotNone(f["total"])                 # итог есть сразу
        net = next(x for x in doc["links"] if x["id"] == "net_q")
        loss = next(x for x in doc["links"] if x["id"] == "loss")
        for x in (net, loss):
            self.assertIsNone(x["fresh"])
            self.assertEqual(x["why"], "young")
            self.assertIn("11", x["why_text"])
            self.assertIn(str(S.WINDOW_HOURS_MIN), x["why_text"])
        self.assertNotEqual(f["weak"], "net_q")
        # без правки: «Сеть» = один DNS 150 мс (≈191) — слабое звено и потолок итога ≈541 (REV-it1 «Дополнение»)
        whole = SA.total(dict((k, v) for k, v in HS.Ctx(S.load_spec(), self.now).view(self.now)["vals"].items()))
        self.assertEqual(whole["weak"], "net_q")
        self.assertGreater(f["total"], whole["total"])

    def test_after_the_window_the_network_counts(self):
        from kaskad import score as S
        self.probes(hours=S.WINDOW_HOURS_MIN)            # окно набрано — пометки нет, «Сеть» из проб
        doc = self.doc()
        self.assertIsNone(doc["provisional"])
        net = next(x for x in doc["links"] if x["id"] == "net_q")
        self.assertIsNotNone(net["fresh"])
        self.assertIsNone(net["why"])

    def test_no_probes_here_no_mark(self):
        from kaskad import score as S
        self.probes(hours=3)
        doc = self.doc(probing=False)                    # пробы выключены или шлюз выключен — «Сеть» только из DNS
        self.assertIsNone(doc["provisional"])
        self.assertEqual(next(x for x in doc["links"] if x["id"] == "net_q")["why"], None)
        self.assertEqual(self.doc()["provisional"]["have_h"], 3)
        self.assertLess(3, S.WINDOW_HOURS_MIN)

    def test_the_probes_run_only_with_the_collector_and_the_key(self):
        on = {"gateway": {"enabled": True, "hostmon": {"enabled": True, "probes": True}}, "engine": "nft"}
        self.assertTrue(HS.probes_run(on))
        for st in ({"gateway": {"enabled": False, "hostmon": {"probes": True}}, "engine": "nft"},
                   {"gateway": {"enabled": True, "hostmon": {"probes": True}}, "engine": "iptables"},
                   {"gateway": {"enabled": True, "hostmon": {}}, "engine": "nft"},
                   {"gateway": {"enabled": True, "hostmon": {"enabled": False, "probes": True}}, "engine": "nft"}, {}):
            self.assertFalse(HS.probes_run(st), st)


class ExitHourTest(ExitLineCase):
    def setUp(self):
        super().setUp()
        api._EXH.update(key=None, body=None)

    def test_minutes_of_every_exit_in_one_answer(self):
        now = 1791190260
        self.minute(now - 120, {E1: {"rx": 750000, "tx": 0, "rtt_med": 42.0, "loss": 0.5, "state": "ok"},
                                E2: {"state": "off"}})
        self.minute(now - 3700, {E1: {"rx": 1, "tx": 1, "state": "ok"}})          # старше часа — нет
        doc = metrics.exit_hour(now)
        self.assertEqual((doc["charts"], doc["step"], doc["n"], doc["t0"]), (True, 60, 60, (now // 60 - 59) * 60))
        e = doc["exits"][E1]
        i = (now - 120 - doc["t0"]) // 60
        self.assertEqual((e["rtt"][i], e["loss"][i], e["mbps"][i], e["st"][i]), (42.0, 0.5, 0.1, 1))
        self.assertEqual(sum(1 for x in e["rtt"] if x is not None), 1)
        self.assertEqual(doc["exits"][E2]["st"][i], 3)
        self.assertIsNone(doc["exits"][E2]["mbps"][i])                           # нет счётчиков — нет трафика

    def test_a_repeated_key_is_left_out(self):
        now = int(time.time())
        self.minute(now - 60, {E1 + "-201": {"rx": 1, "tx": 1, "state": "ok"}})
        self.assertEqual(metrics.exit_hour(now)["exits"], {})

    def test_route_admin_get_etag_and_foreign_input(self):
        r, _m = routes.find("GET", "/api/gw/exits/hour")
        self.assertEqual((r.auth, r.runs), ("admin", False))
        self.assertEqual(self.call("GET", "/api/gw/exits/hour", tunnel=False).status, 401)
        now = int(time.time())
        self.minute(now - 60, {E1: {"rx": 5, "tx": 5, "state": "ok"}})
        self.live(metrics={"ts": now // 60 * 60 - 60})
        r = self.call("GET", "/api/gw/exits/hour")
        self.assertEqual(r.status, 200)
        tag = r.headers.get("etag")
        self.assertTrue(tag and tag.startswith('"exh-'))
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertIn(E1, r.json()["exits"])
        r2 = self.call("GET", "/api/gw/exits/hour", headers={"If-None-Match": tag})
        self.assertEqual((r2.status, r2.body), (304, b""))
        self.assertEqual(self.call("GET", "/api/gw/exits/hour?range=1d").status, 400)

    def test_without_sqlite_base_charts_false(self):
        from unittest import mock
        with mock.patch.object(metrics, "_reader", lambda: None):
            self.assertEqual(metrics.exit_hour(1791190260)["charts"], False)


class HealthLimitsTest(HostPanelCase):
    def test_every_check_has_the_thresholds_of_its_rule(self):
        h = dict(BOT, by="collector", instant=False, level="warn",
                 checks={"cpu": {"level": "ok", "value": 12.0}, "mem": {"level": "ok", "value": 40.0},
                         "disk": {"level": "warn", "value": 92.0}, "retrans": {"level": "ok", "value": 0.3},
                         "x_unknown": {"level": "ok"}})
        self.live_json(h)
        c = self.ok("GET", "/api/host/health")["health"]["checks"]
        self.assertEqual((c["cpu"]["warn_at"], c["cpu"]["crit_at"], c["cpu"]["down"]), (85, 95, False))
        self.assertEqual((c["mem"]["warn_at"], c["mem"]["crit_at"], c["mem"]["down"], c["mem"]["crit_draft"]),
                         (10, 5, True, True))
        self.assertEqual((c["disk"]["warn_at"], c["disk"]["crit_at"]), (90, 98))
        self.assertEqual(c["retrans"]["warn_at"], hostmon.RULES["retrans"]["warn"][0])
        self.assertEqual(c["x_unknown"], {"level": "ok"})                          # чужое — как есть
        self.assertEqual(c["disk"]["value"], 92.0)


class SynthBoundaryTest(unittest.TestCase):
    """Балл 0–1000 — только панель (решение Т1): его не зовут CLI, MCP, бот и «Поделиться»; в панели — только часть ovb."""

    def test_only_the_panel_route_and_the_ovb_part(self):
        import os
        import re
        lib = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad")
        users = set()
        for d, _dirs, names in os.walk(lib):
            for n in names:
                if not n.endswith((".py", ".js")):
                    continue
                p = os.path.join(d, n)
                with open(p, encoding="utf-8") as f:
                    s = f.read()
                if re.search(r"node_synth|synth_doc|score_abs|/api/host/synth|host\.synth\(", s):
                    users.add(os.path.relpath(p, lib))
        self.assertEqual(users, {"score_abs.py", "bench/hostscore.py", "panel/host.py", "panel/static/app.js",
                                 "panel/static/pages/ovb.js", "panel/static/dev-mock.js"})
