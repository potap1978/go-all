"""Круг 1, К1 (владелец 28.10: «бери тестовые серверы с 1 и с 2 CPU и на них»): калибровка якорей ``kaskad.score_abs``.

Данные — tests/fixtures/score_abs_calib_k1.json: сырые полные проверки трёх эталонов Aeza (Москва) без адресов и имён
серверов — 1 vCPU / 2 ГБ общий, 2 vCPU / 4 ГБ общий, выделенное ядро Ryzen 9 9950X (волна 1 B9). Тест выводит якоря
из сырых значений по правилу К1 (оно же записано в файле) и сверяет с константами модуля; тесты, которые К1 не
калибровал, — на якорях исследования. Отчёт — k3/stand/logs/K-it1-calib.md.
"""

import json
import math
import os
import re
import statistics
import unittest

from kaskad import score_abs as SA

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "score_abs_calib_k1.json")
PER_CORE = ("cpu_1t", "chacha_1t", "aes_1t", "mem_bw")     # одно ядро: 900 — выделенное ядро
FUNCTIONAL_100 = ("chacha_1t", "aes_1t")                     # 100 — порог «VPN страдает», не машина
CALIBRATED_TESTS = PER_CORE + ("cpu_mt",)


def _data():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def _med(machine, tid):
    return statistics.median(r["vals"][tid] for r in machine["runs"] if tid in r["vals"])


# что не должно попасть в файл эталонов (REV-it1 «Дополнение» №9): адреса IPv4 и IPv6, домены, номера серверов Aeza
# (20xxxxx), имена стенда; «.md» в source — имя отчёта, не домен
LEAKS = (re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
         re.compile(r"(?i)(?<![\w:])(?:[0-9a-f]{1,4}:){2,7}[0-9a-f]{0,4}|(?<![\w:])(?:[0-9a-f]{1,4}:)+:[0-9a-f]{0,4}"),
         re.compile(r"(?i)\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|ru|net|org|io)\b"),
         re.compile(r"\b20\d{5}\b"), re.compile(r"claude-test|\"ip\"|ptr\.network"))


def _leaks(raw):
    return [m.group(0) for rx in LEAKS for m in rx.finditer(raw)]


def _sig2(x):
    e = math.floor(math.log10(abs(x))) - 1
    return round(x / 10 ** e) * 10 ** e


def derive(d, tid):
    """Якоря 100/500/700/900 теста ``tid`` по правилу К1 из сырых значений файла."""
    r = dict(zip(SA.A, d["research_anchors"][tid]))
    m = d["machines"]
    if tid in PER_CORE:
        top = _med(m["lucky_ded1"], tid)
        k = top / r[900]
        a = [r[100] * k, r[500] * k, r[700] * k, top]
        if tid in FUNCTIONAL_100:
            a[0] = r[100]
    else:
        base, good = _med(m["base_1vcpu"], tid), _med(m["good_2vcpu"], tid)
        a = [base * r[100] / r[500], base, good, good * r[900] / r[700]]
    return [_sig2(x) for x in a]


def anchors(tid):
    return [SA.TESTS[tid]["anchors"][a] for a in SA.A]


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        self.d = _data()

    def test_flag_and_version(self):
        self.assertEqual(SA.VERSION, self.d["version"])
        self.assertEqual((SA.A, SA.MARGIN), ((100, 500, 700, 900), 350))

    def test_calibration_is_per_link(self):
        """Владелец 28.10, решение 5 (REV-it1 «Дополнение» №7): «выверено» — у звена, все тесты которого сняты с
        эталонов К1; остальные — «предварительно». Раньше один флаг ``CALIBRATED = True`` снимал пометку со всей шкалы,
        хотя из серверов взят только процессор (и mem_bw — вне звеньев радара)."""
        self.assertEqual(set(SA.CALIB_TESTS), set(CALIBRATED_TESTS))          # то, что тест выводит из сырых данных
        self.assertEqual(SA.CALIB_LINKS, ("cpu", "crypto"))
        for lid in SA.LINK_IDS:
            tids = SA.link_tests(lid)
            self.assertTrue(tids, lid)
            self.assertEqual(SA.link_calibrated(lid), all(t in CALIBRATED_TESTS for t in tids), lid)
        self.assertIs(SA.CALIBRATED, False)                                    # шкала целиком — ещё нет
        self.assertEqual((SA.link_tests("nope"), SA.link_calibrated("nope")), ((), False))

    def test_the_reference_servers_are_those_of_the_file(self):
        """REFS модуля — те же машины, что в файле К1, без адресов и номеров серверов (RESEARCH §4.1). Только внутри
        кода: панель и API их не показывают (владелец 29.10, D-142)."""
        m = self.d["machines"]
        want = [("base_1vcpu", 500, "all", False), ("good_2vcpu", 700, "all", False), ("lucky_ded1", 900, "core", True)]
        self.assertEqual(len(SA.REFS), len(want))
        for ref, (key, anchor, scope, ded) in zip(SA.REFS, want):
            x = m[key]
            self.assertEqual((ref["anchor"], ref["scope"], ref["dedicated"], ref["vcpu"]), (anchor, scope, ded, x["nproc"]))
            self.assertEqual(ref["hoster"], x["hoster"])
            self.assertTrue(x["tariff"].startswith(ref["tariff"] + ":"), (ref["tariff"], x["tariff"]))
            self.assertIn(ref["cpu"], x["cpu"])
            self.assertEqual((ref["city"], x["city"]), ("msk", "Москва"))
            self.assertIn(ref["date"], x["dates"])
        self.assertEqual(_leaks(json.dumps(SA.REFS, ensure_ascii=False)), [])

    def test_the_constants_come_from_the_raw_data(self):
        for tid in CALIBRATED_TESTS:
            self.assertEqual(anchors(tid), derive(self.d, tid), tid)

    def test_the_other_tests_keep_the_research_anchors(self):
        self.assertEqual(set(self.d["research_anchors"]), set(SA.TESTS))
        for tid in SA.TESTS:
            if tid not in CALIBRATED_TESTS:
                self.assertEqual(anchors(tid), self.d["research_anchors"][tid], tid)

    def test_enough_checks_and_no_addresses(self):
        m = self.d["machines"]
        self.assertGreaterEqual(min(len(m[k]["runs"]) for k in ("base_1vcpu", "good_2vcpu")), 6)
        self.assertGreaterEqual(len(m["lucky_ded1"]["runs"]), 2)
        self.assertEqual((m["base_1vcpu"]["nproc"], m["good_2vcpu"]["nproc"], m["lucky_ded1"]["nproc"]), (1, 2, 1))
        self.assertEqual(_leaks(json.dumps(self.d, ensure_ascii=False)), [])

    def test_the_leak_check_catches_addresses(self):
        """REV-it1 «Дополнение» №9: прежняя проверка искала три подстроки и пропускала IPv4, IPv6, домен и номер
        сервера в ``source`` или ``tariff``. Каждая такая строка в копии файла — находка."""
        for bad in ("1.2.3.4", "2a01:4f8:c0c:1234::1", "fe80::1", "node.example.com", "vpn.example.ru", "x.example.net",
                    "a.example.org", "my.host.io", "server 2017651", "claude-test-2", "ptr.network"):
            d = json.loads(json.dumps(self.d))
            d["machines"]["base_1vcpu"]["source"] = "Aeza " + bad
            self.assertTrue(_leaks(json.dumps(d, ensure_ascii=False)), bad)
        d = json.loads(json.dumps(self.d))
        d["machines"]["good_2vcpu"]["ip"] = "x"                                        # поле адреса — тоже
        self.assertTrue(_leaks(json.dumps(d, ensure_ascii=False)))
        self.assertEqual(_leaks('{"source": "K-it1-calib.md", "v": 3.3, "n": 2026, "t": "2026-10-28", "x": 9950}'), [])

    def _group(self, key, gid):
        m = self.d["machines"][key]
        vals = dict((t, _med(m, t)) for t in SA.TESTS if any(t in r["vals"] for r in m["runs"]))
        return SA.total(vals)["groups"][gid], vals

    def test_1_and_2_cpu_get_sensible_scores_and_a_difference(self):
        """Эталоны попадают в свои якоря: все ядра 1 vCPU ≈ 500, 2 vCPU ≈ 700, выделенное ядро на ядро ≈ 900;
        процессор 2 vCPU выше 1 vCPU, и разница заметна (≥ 30), но не в разы.

        Группа «Процессор» базового эталона — ≈ 790 (777–795 по 8 проверкам), а не 430–570 RESEARCH §4.4: это
        отступление принял владелец 28.10 (решение 5, D-140, SPEC-node-score NS-D36) — «базовый» по процессору задаёт
        cpu_mt (все ядра ≈ 500), шкала «на ядро» выше: ядро общего тарифа то же Ryzen 9 9950X, что у выделенного.
        Тест держит именно это решение: сдвиг якорей, уводящий базовый эталон из 777–795, — новое решение, не правка
        теста (REV-it1 «Дополнение» №8)."""
        one, v1 = self._group("base_1vcpu", "cpu")
        two, v2 = self._group("good_2vcpu", "cpu")
        _ded, vd = self._group("lucky_ded1", "cpu")
        self.assertTrue(480 <= SA.score_of("cpu_mt", v1["cpu_mt"]) <= 520)
        self.assertTrue(680 <= SA.score_of("cpu_mt", v2["cpu_mt"]) <= 720)
        for tid in PER_CORE:
            self.assertTrue(880 <= SA.score_of(tid, vd[tid]) <= 920, tid)
        self.assertGreaterEqual(two - one, 30)
        self.assertTrue(one < two <= 900, (one, two))
        self.assertTrue(777 <= one <= 795, one)                              # решение владельца 28.10, не §4.4
        runs = [SA.total(dict(r["vals"]))["groups"]["cpu"] for r in self.d["machines"]["base_1vcpu"]["runs"]]
        self.assertTrue(all(777 <= x <= 795 for x in runs), runs)

    def test_the_weak_link_is_not_the_processor_of_a_cheap_tariff(self):
        """Шифрование на общем ядре 1 vCPU — не «слабое звено» (выше 700): ядро то же, что у выделенного."""
        _g, v1 = self._group("base_1vcpu", "cpu")
        self.assertGreater(SA.score_of("chacha_1t", v1["chacha_1t"]), 700)


if __name__ == "__main__":
    unittest.main()
