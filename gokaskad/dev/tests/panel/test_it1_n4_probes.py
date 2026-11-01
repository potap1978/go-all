"""Круг 1, Н-4 (стенд T-it1b): «Отклик» и «Дрожание» на «Сейчас» стояли «ждём проб», хотя пробы шли.

Сборщик (``gw.hostmon.ping_rows``) пишет в строку узла ``*`` только потери: rtt_med / rtt_p90 / jit — None, jit_n — 0.
Звено брало отклик и дрожание только из ``*`` — значения не было никогда. ``hostscore.read`` теперь дополняет пустые
поля ``*`` из строк целей того же ts по правилу самой ``*`` (``hostmon._reduce``); хранимые данные не меняются, поэтому
работает и на уже накопленной истории.
"""

import os
import unittest
from unittest import mock

from kaskad.bench import hostscore as HS
from kaskad.gw import hostmon, metrics
from tests.panel.test_panel_score import DAY, ScoreCase

# цели узла стенда 2012893 (снимок s-after4: rtt 1.1.1.1 ≈ 20, 77.88.8.8 ≈ 2,7, 8.8.8.8 ≈ 20,8 мс); дрожание — оценка
REAL = {"1.1.1.1": (19.97, 0.6), "77.88.8.8": (2.66, 0.2), "8.8.8.8": (20.77, 0.7)}


def _row(anchor, ts, n=1080, rtt=None, p90=None, jit=None, jit_n=0, lost=0.0):
    return {"anchor": anchor, "ts": ts, "n": n, "lost": lost, "runs": 0, "rtt_med": rtt, "rtt_p90": p90, "jit": jit,
            "jit_n": jit_n}


class NodeFillTest(unittest.TestCase):
    """Чистая функция: правило строки узла, пустые поля только, базу не трогает."""

    def test_three_anchors_drop_the_worst(self):
        rows = [_row("*", 0)] + [_row(a, 0, rtt=r, p90=r + 5, jit=j, jit_n=1000) for a, (r, j) in REAL.items()]
        node = HS.node_fill(rows)[0]
        self.assertAlmostEqual(node["rtt_med"], (19.97 + 2.66) / 2)     # худшая (20,77) отброшена — как у потерь
        self.assertAlmostEqual(node["rtt_p90"], (24.97 + 7.66) / 2)
        self.assertAlmostEqual(node["jit"], (0.6 + 0.2) / 2)
        self.assertEqual(node["jit_n"], 3000)
        self.assertEqual(node["rtt_med"], hostmon._reduce([19.97, 2.66, 20.77]))
        self.assertIsNone(rows[0]["rtt_med"])                           # входные строки не изменены

    def test_two_one_and_none(self):
        two = HS.node_fill([_row("*", 0), _row("a", 0, rtt=30.0), _row("b", 0, rtt=10.0)])[0]
        self.assertEqual(two["rtt_med"], 10.0)                          # 2 — лучшая
        one = HS.node_fill([_row("*", 0), _row("a", 0, rtt=30.0), _row("b", 0, n=0, rtt=1.0)])[0]
        self.assertEqual(one["rtt_med"], 30.0)                          # цель без пакетов не считается
        self.assertIsNone(one["jit"])                                   # у целей нет дрожания — пусто
        self.assertEqual(one["jit_n"], 0)
        none = HS.node_fill([_row("*", 0), _row("a", 3600, rtt=30.0)])[0]
        self.assertIsNone(none["rtt_med"])                              # цели другого часа — не берутся

    def test_own_value_is_kept(self):
        rows = [_row("*", 0, rtt=50.0, jit=3.0, jit_n=10), _row("a", 0, rtt=1.0, p90=2.0, jit=0.1, jit_n=1000),
                _row("b", 0, rtt=1.0, jit=0.1, jit_n=1000)]
        node = HS.node_fill(rows)[0]
        self.assertEqual((node["rtt_med"], node["jit"], node["jit_n"]), (50.0, 3.0, 10))
        self.assertEqual(node["rtt_p90"], 2.0)                          # пустое поле — дополнено


class ProbesLinkTest(ScoreCase):
    """Через панель: строки как пишет сборщик (``*`` только с потерями) → у «Отклика» и «Дрожания» есть значение."""

    def collector_probes(self, hours=72, m10=False):
        w = metrics.open_writer()
        try:
            with w.conn:
                for table, step, count in (("host_ping_h1", 3600, hours),) + ((("host_ping_m10", 600, 6 * 24),)
                                                                              if m10 else ()):
                    k = step // 60
                    for i in range(1, count + 1):
                        ts = (self.now // step - i) * step
                        for a, (rtt, jit) in REAL.items():
                            w.conn.execute("INSERT OR REPLACE INTO %s (anchor, ts, n, lost, runs, rtt_med, rtt_p90, "
                                           "jit, jit_n) VALUES (?,?,?,?,?,?,?,?,?)" % table,
                                           (a, ts, 18 * k, 0.0, 0, rtt, rtt + 3, jit, 17 * k))
                        w.conn.execute("INSERT OR REPLACE INTO %s (anchor, ts, n, lost, runs, rtt_med, rtt_p90, jit, "
                                       "jit_n) VALUES ('*', ?, ?, 0, 0, NULL, NULL, NULL, 0)" % table, (ts, 54 * k))
                    w.conn.execute("INSERT OR REPLACE INTO host_h1 (ts, n, busy, busy_max, steal, warn_min, alarm_min)"
                                   " VALUES (?,?,?,?,?,?,?)", ((self.now // 3600 - 1) * 3600, 60, 10.0, 30.0, 0.2, 0, 0))
        finally:
            w.close()

    def parts(self):
        doc = self.score()
        return dict((p["test"], p) for lk in doc["links"] if lk["id"] == "net_q" for p in lk.get("parts") or ())

    def test_rtt_and_jitter_from_the_anchors(self):
        self.collector_probes()
        with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1"}):
            ps = self.parts()
        for tid in ("rtt", "jitter"):                                   # без правки: fresh_raw None, why «probes»
            self.assertIsNotNone(ps[tid]["fresh_raw"], tid)
            self.assertEqual((ps[tid].get("why"), ps[tid].get("why_text")), (None, None), tid)
        self.assertAlmostEqual(ps["rtt"]["fresh_raw"], (19.97 + 2.66) / 2, places=2)
        self.assertAlmostEqual(ps["jitter"]["fresh_raw"], 0.4, places=2)
        self.assertIsNotNone(ps["loss"]["fresh_raw"])                   # потери — как были

    def test_day_page_has_the_delay(self):
        self.collector_probes(hours=48, m10=True)
        day = HS.Ctx(self.spec(), self.now).day_of(self.now - DAY)
        doc = self.ok("GET", "/api/host/day/" + day)
        self.assertTrue(any(x is not None for x in doc["series"]["rtt"]))     # кривая дня (m10) — с откликом
        self.assertIsNotNone(doc["metrics"]["rtt_med"])
        self.assertIsNotNone(doc["metrics"]["jit"])

    def spec(self):
        from kaskad import score as S
        return S.load_spec()

    def test_true_waiting_when_no_probes_at_all(self):
        for lang in ("ru", "en"):
            with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1", "KASKAD_LANG": lang}):
                from kaskad import i18n
                with i18n.use_lang(lang):
                    ps = self.parts()
                    for tid in ("rtt", "jitter"):
                        self.assertIsNone(ps[tid]["fresh_raw"])
                        self.assertEqual(ps[tid]["why"], "probes")
                        self.assertEqual(ps[tid]["why_text"], i18n.tr("score.link.why.probes", lang))
                        self.assertTrue(ps[tid]["why_text"])

    def test_texts_in_both_languages(self):
        self.collector_probes()
        from kaskad import i18n
        for lang in ("ru", "en"):
            with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1", "KASKAD_LANG": lang}), i18n.use_lang(lang):
                ps = self.parts()
                self.assertIsNotNone(ps["rtt"]["fresh_raw"], lang)
                self.assertIsNone(ps["rtt"].get("why"), lang)


if __name__ == "__main__":
    unittest.main()
