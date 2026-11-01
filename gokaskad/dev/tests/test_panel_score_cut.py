"""Сокращение оценки сервера, панель (решение владельца 23.10; NS-D30–NS-D34, k3/handoff/SPEC-node-score.md;
контракт API — k3/scratchpad/score-cut/CONTRACT.md):

* тон события ``bench_done`` в ленте — по итогу проверки ``args.status`` (failed — danger, partial — warning, ok —
  success), а не по ключу события: события, записанные до обновления, несут старые ключи (``bench_barely`` —
  «почти не работает»), а у новых ключа «плохо» нет вовсе; без args — по ключу (bench_failed / bench_partial);
* сторож: в diag.js, app.js, словарях «Диагностики» и dev-mock-diag.js нет шкалы 0–1000, поясов (bandOf, «хороший»,
  «слабый», «почти не работает»), звезды (gx-star), «слабого звена» и обращений к удалённым полям ответа
  (score.*, fresh_total, band, weakest, capacity, fit_for, offline_budget, кривые /api/host/curves, баллы звеньев);
  синонимы поиска — без «балл, оценка, звезда»;
* поля, которые панель читает у звеньев «Сейчас» и дня, есть в ответе ядра (hostscore.LINK_FIELDS / DAY_LINK_FIELDS)."""

import json
import os
import re
import shutil
import subprocess
import unittest

from tests import jsparts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "lib", "kaskad", "panel", "static")
HAS_NODE = shutil.which("node") is not None


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def strip_comments(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", js)


def looks(app, events):
    """evLook из ``app`` (текст app.js) для ``events`` -> [[значок, тон], …] — node, как tests/test_ui2_layout.py."""
    js = "\n".join(jsparts.const(app, n) for n in ("EV_ICON", "EV_LOOK", "HOST_LOOK", "evLook")) + r"""
console.log(JSON.stringify(JSON.parse(process.argv[1]).map((e) => evLook(e))));
"""
    res = subprocess.run(["node", "-e", js, json.dumps(events)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if res.returncode != 0:
        raise AssertionError(res.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(res.stdout.decode("utf-8"))


def ev(status, key):
    """Событие ленты ``bench_done`` как его пишет gw.live (bench_key / bench_args); key — короткое имя ключа."""
    args = {"ts": 1760900000, "id": "abcdef0123", "mode": "quick", "by": "schedule", "status": status, "why": None,
            "rel": 96 if status == "ok" else None}
    return {"kind": "bench_done", "key": "gw_live.event." + key, "args": args}


@unittest.skipUnless(HAS_NODE, "нет node")
class BenchDoneToneTest(unittest.TestCase):
    """Каждый случай ниже на старом evLook (тон по ключу, f7560ed5) даёт другой тон: старый ключ события и итог
    проверки расходятся — так выглядят события ленты, записанные до обновления."""

    def tone(self, e):
        return looks(read("app.js"), [e])[0]

    def test_ok_is_success_whatever_the_old_key(self):
        # старый ключ «почти не работает» (bench_barely) у удачной проверки: раньше — warning
        self.assertEqual(self.tone(ev("ok", "bench_barely")), ["stethoscope", "success"])

    def test_partial_is_warning(self):
        # неполная проверка со старым ключом «итог» (bench_done): раньше — success
        self.assertEqual(self.tone(ev("partial", "bench_done")), ["stethoscope", "warning"])

    def test_failed_is_danger(self):
        # не удалась, ключ без числа (bench_done_bare): раньше — success
        self.assertEqual(self.tone(ev("failed", "bench_done_bare")), ["stethoscope", "danger"])

    def test_the_keys_of_now_agree_and_the_key_is_the_fallback(self):
        """Новые ключи (gw.live.bench_key) совпадают с итогом; без args тон — по ключу."""
        got = looks(read("app.js"), [ev("ok", "bench_done"), ev("ok", "bench_done_bare"), ev("partial", "bench_partial"),
                                     ev("failed", "bench_failed"),
                                     {"kind": "bench_done", "key": "gw_live.event.bench_failed"},
                                     {"kind": "bench_done", "key": "gw_live.event.bench_partial"},
                                     {"kind": "bench_done", "key": "gw_live.event.bench_done"},
                                     {"kind": "bench_done"}])
        self.assertEqual([x[1] for x in got], ["success", "success", "warning", "danger", "danger", "warning", "success", "success"])


FILES = ("pages/diag.js", "app.js", "pages/diag-i18n-ru.js", "pages/diag-i18n-en.js", "dev-mock-diag.js")
# слова и имена абсолютной оценки (NS-D34): шкала, пояса, звезда, «слабое звено», «за свои деньги»
WORDS = (r"0[–-]1000", r"\bbandOf\b", r"gx-star", r"слабое звено", r"\bхороший\b", r"\bслабый\b", r"почти не работает",
         r"за свои деньги", r"\bbench_barely")
# удалённые поля ответа (CONTRACT.md «УДАЛЕНО»): объект score и его поля, ёмкость, «годится для…», бюджет, кривые;
# api.score / host.score — имя вызова GET /api/host/score, не поле
FIELDS = (r"\b(?!api\b|host\b)[A-Za-z_]\w*\.score\b", r"\bfresh_total\b", r"\.band\b", r"\bweakest\b", r"\bcapacity\b",
          r"\bfit_for\b", r"\boffline_budget\b", r"\blast_total\b", r"\brel_prelim\b", r"/api/host/curves", r"\bcurves\b")
# баллы звеньев (links[].fresh/now/usual/p25/p75, runs[].usual) — там, где экран читает звенья и проверки
# (Date.now — часы, "diag.now" — якорь поиска вкладки)
LINK_FIELDS = (r"(?<!Date)(?<!diag)\.(fresh|now|usual|p25|p75)\b", r"\bscore\s*:")


class GuardTest(unittest.TestCase):
    def text(self, name):
        s = read(name)
        return strip_comments(s) if name.endswith(".js") and "i18n" not in name else s

    def test_no_absolute_score_words(self):
        for name in FILES:
            s = self.text(name)
            for rx in WORDS:
                self.assertIsNone(re.search(rx, s, flags=re.I), "%s: %s" % (name, rx))

    def test_no_removed_fields(self):
        for name in FILES:
            s = self.text(name)
            for rx in FIELDS:
                m = re.search(rx, s)
                self.assertIsNone(m, "%s: %s — %s" % (name, rx, m and s[max(0, m.start() - 60):m.end() + 20]))
        for name in ("pages/diag.js", "dev-mock-diag.js"):
            s = self.text(name)
            for rx in LINK_FIELDS:
                m = re.search(rx, s)
                self.assertIsNone(m, "%s: %s — %s" % (name, rx, m and s[max(0, m.start() - 60):m.end() + 20]))

    def test_no_score_in_the_texts_of_diag(self):
        """Тексты «Диагностики» — без балла и звезды: RU «балл», «звезд»; EN score, star, rating."""
        for name, rxs in (("pages/diag-i18n-ru.js", (r"балл", r"звезд")), ("pages/diag-i18n-en.js", (r"\bscore", r"\bstar\b", r"\brating"))):
            s = read(name)
            for rx in rxs:
                self.assertIsNone(re.search(rx, s, flags=re.I), "%s: %s" % (name, rx))

    def test_search_synonyms(self):
        """find.diag…Syn (части «Проверки сервера»): без «балл, оценка, звезда» (score, rating, star) — у «Сейчас» есть
        «от обычного». find.diagSyn ушёл с пунктом «Диагностика» из меню (UI-A1: части «Обзора»)."""
        for name, bad, good in (("i18n-ru.js", (r"балл", r"оценк", r"звезд"), "от обычного"),
                                ("i18n-en.js", (r"\bscore", r"\brating", r"\bstar\b"), "of usual")):
            vals = dict(re.findall(r'^\s*"(find\.diag\w*Syn)":\s*"(.*)",?$', read(name), flags=re.M))
            self.assertIn("find.diagNowSyn", vals, name)
            self.assertNotIn("find.diagSyn", vals, name)
            for k, v in vals.items():
                for rx in bad:
                    self.assertIsNone(re.search(rx, v, flags=re.I), "%s %s: %s" % (name, k, rx))
            self.assertIn(good, vals["find.diagNowSyn"], name)

    def test_the_star_styles_are_gone(self):
        self.assertNotIn("gx-star", read("components.css"))

    def test_the_fields_the_panel_reads_are_in_the_answer(self):
        """Звенья «Сейчас» и дня: панель читает только поля ответа ядра (hostscore.LINK_FIELDS / DAY_LINK_FIELDS)."""
        from kaskad.bench import hostscore as HS
        for f in ("id", "rel", "test", "fresh_raw", "usual_raw", "usual_n", "unit", "dev", "parts"):
            self.assertIn(f, HS.LINK_FIELDS, f)
        for f in ("id", "rel", "test", "fresh_raw", "usual_raw", "usual_n", "unit"):
            self.assertIn(f, HS.DAY_LINK_FIELDS, f)


if __name__ == "__main__":
    unittest.main()
