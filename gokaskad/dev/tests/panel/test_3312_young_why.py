"""Т-3312 «Мелочь 2» (стенд, чистый узел d17d4df4): первые 12 ч «Проверка сервера › Сейчас» у «Отклика», «Дрожания» и
«Потерь» писала «ждём проб» (why "probes"), хотя пробы шли раз в минуту, а «Почему итог не выше» рядом — «0 ч из 12».

Пока окно проб набирается (``provisional`` — {why "net_young", have_h, need_h}, D-141: S.WINDOW_HOURS_MIN), у этих строк
код young и текст «ещё измеряется: N ч из 12» — часы из правила, не числом в тексте; ``provisional`` — в ответе
/api/host/score тем же видом, что у /api/host/synth. Старый узел (окно набрано) без значений и узел, где пробы не
идут, — «ждём проб», как было (Н-4).
"""

import os
import re
import shutil
import unittest
from unittest import mock

from kaskad import i18n
from kaskad import score as S
from kaskad import score_abs as SA
from kaskad.bench import hostscore as HS
from kaskad.gw import metrics
from tests.panel.test_panel_score import ScoreCase


def rows_of(doc):
    """{test: row} строк «Сейчас» «Отклик», «Дрожание» (части «Сети») и «Потери» (звено) — как CROWS панели."""
    out = {}
    for lk in doc["links"]:
        if lk["id"] == "net_q":
            out.update((p["test"], p) for p in lk.get("parts") or () if p.get("test") in ("rtt", "jitter"))
        elif lk["id"] == "loss":
            out["loss"] = lk
    return out


class YoungWhyTest(ScoreCase):

    def doc(self, probing=True):
        return HS.score_doc(S.load_spec(), now=self.now, probing=probing)

    def test_young_tests_are_the_probe_tests_of_the_young_links(self):
        """YOUNG_TESTS — тесты проб (S.PROBE_TESTS) звеньев YOUNG_LINKS итога; новый тест там — сюда же."""
        tests = set()
        for b in SA.LINKS:
            if b["id"] in HS.YOUNG_LINKS:
                tests.update(b.get("tests") or ())
                tests.update(t["id"] for g in SA.GROUPS if g["id"] in (b.get("groups") or ()) for t in g["tests"])
        self.assertEqual(sorted(HS.YOUNG_TESTS), sorted(tests & set(S.PROBE_TESTS)))
        self.assertIn("young", HS.WHY_CODES)

    def test_clean_node_first_hours(self):
        """Без правки: why "probes" — «ждём проб». Часов проб нет совсем (как на стенде) и 11 из 12."""
        for hours in (0, S.WINDOW_HOURS_MIN - 1):
            with self.subTest(hours=hours):
                if hours:
                    self.probes(hours=hours)
                for lang in ("ru", "en"):
                    with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1", "KASKAD_LANG": lang}), i18n.use_lang(lang):
                        doc = self.doc()
                        want = i18n.tr("score.link.why.young", lang, have=str(hours), need=str(S.WINDOW_HOURS_MIN))
                    self.assertEqual(doc["provisional"], {"why": "net_young", "have_h": hours,
                                                          "need_h": S.WINDOW_HOURS_MIN})
                    rows = rows_of(doc)
                    self.assertEqual(sorted(rows), ["jitter", "loss", "rtt"])
                    for tid, x in rows.items():
                        self.assertIsNone(x["fresh_raw"], (tid, lang))
                        self.assertEqual((x["why"], x["why_text"]), ("young", want), (tid, lang))
                    self.assertIn("%d %s" % (hours, "ч" if lang == "ru" else "h"), want)
                    self.assertIn(str(S.WINDOW_HOURS_MIN), want)
                    self.assertNotIn("{", want)
                    # тот же вид и те же часы, что у «Почему итог не выше» (synth_doc)
                    syn = HS.synth_doc(S.load_spec(), now=self.now, probing=True)
                    self.assertEqual(syn["provisional"], doc["provisional"])

    def test_old_node_without_values_still_awaits_probes(self):
        """Окно набрано, а отклика и дрожания нет (строки узла до Н-4: у ``*`` только потери, целей нет) — «ждём проб»."""
        w = metrics.open_writer()
        try:
            with w.conn:
                for i in range(1, 2 * S.WINDOW_HOURS_MIN + 1):
                    w.conn.execute("INSERT OR REPLACE INTO host_ping_h1 (anchor, ts, n, lost, runs, rtt_med, rtt_p90, "
                                   "jit, jit_n) VALUES ('*', ?, 3240, 0, 0, NULL, NULL, NULL, 0)",
                                   ((self.now // 3600 - i) * 3600,))
        finally:
            w.close()
        with mock.patch.dict(os.environ, {"K3_TR_STRICT": "1"}):
            doc = self.doc()
        self.assertIsNone(doc["provisional"])
        rows = rows_of(doc)
        for tid in ("rtt", "jitter"):
            self.assertIsNone(rows[tid]["fresh_raw"], tid)
            self.assertEqual((rows[tid]["why"], rows[tid]["why_text"]), ("probes", i18n.tr("score.link.why.probes")), tid)
        self.assertIsNotNone(rows["loss"]["fresh_raw"])                 # потери — есть, причины нет
        self.assertIsNone(rows["loss"]["why"])

    def test_probes_off_still_awaits_probes(self):
        """Пробы на узле не идут (шлюз или пробы выключены) — окна не ждём: «ждём проб», как было."""
        self.probes(hours=S.WINDOW_HOURS_MIN - 1)
        doc = self.doc(probing=False)
        self.assertIsNone(doc["provisional"])
        for tid, x in rows_of(doc).items():
            self.assertEqual(x["why"], "probes", tid)

    def test_values_have_no_reason(self):
        """Окно набрано — значения есть, ни young, ни пометки."""
        self.probes(hours=S.WINDOW_HOURS_MIN + 2)
        doc = self.doc()
        self.assertIsNone(doc["provisional"])
        for tid, x in rows_of(doc).items():
            self.assertIsNotNone(x["fresh_raw"], tid)
            self.assertIsNone(x["why"], tid)

    def test_the_panel_api_asks_whether_the_probes_run(self):
        """GET /api/host/score (панель, CLI ``host score``, MCP ``host_score``) — ``node_doc`` берёт probing из
        состояния, как /api/host/synth."""
        with mock.patch.object(HS, "probes_run", return_value=True):
            doc = self.score()
        self.assertEqual(doc["provisional"]["why"], "net_young")
        self.assertEqual(rows_of(doc)["rtt"]["why"], "young")
        self.assertIsNone(self.score()["provisional"])                 # пробы не идут (шлюз выключен) — как было


@unittest.skipUnless(shutil.which("node"), "нет node")
class YoungPanelTest(unittest.TestCase):
    """Панель (diag.js): ячейка «Сейчас» — «ещё измеряется: N ч из 12» по provisional ответа; без часов — без чисел."""

    def test_cell(self):
        from tests.test_it1_logic_panel import STUB, read, run
        from tests import jsparts
        js = read("pages/diag.js")
        parts = [jsparts.const(js, "WHY"), jsparts.const(js, "whyShort"), jsparts.function(js, "rowOf"),
                 jsparts.function(js, "youngOf")]
        out = run(STUB, parts, r"""
const L = (prov) => ({ provisional: prov, links: [{ id: "net_q", source: "probes", parts: [
  { test: "rtt", fresh_raw: null, why: "young", why_text: "Ещё измеряется" }, { test: "jitter", fresh_raw: null, why: "probes" }] }] });
const one = (doc, test) => whyShort(rowOf(doc, { id: test, link: "net_q", test }));
console.log(JSON.stringify({ young: one(L({ why: "net_young", have_h: 0, need_h: 12 }), "rtt"),
  noHours: one(L(null), "rtt"), bad: one(L({ have_h: "5", need_h: 12 }), "rtt"),
  probes: one(L({ why: "net_young", have_h: 0, need_h: 12 }), "jitter") }));
""")
        self.assertEqual(out["young"], 'dg.why.youngH{"have":"0","need":"12"}')
        self.assertEqual(out["noHours"], "dg.why.young")
        self.assertEqual(out["bad"], "dg.why.young")                   # чужой вид часов — без чисел
        self.assertEqual(out["probes"], "dg.why.probes")               # чужой код части — свои слова

    def test_both_languages(self):
        from tests.test_it1_logic_panel import read
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(dg\.why\.\w+)": "([^"]+)"', read("pages/diag-i18n-%s.js" % lang)))
            self.assertTrue(words.get("dg.why.young"), lang)
            self.assertEqual(sorted(re.findall(r"\{(\w+)\}", words.get("dg.why.youngH", ""))), ["have", "need"], lang)


if __name__ == "__main__":
    unittest.main()
