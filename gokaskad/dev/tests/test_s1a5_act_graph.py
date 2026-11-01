"""А5 (27.10), «Кардиограмма», часть 2: окно «График» активности и линия выходов против эталона Claude Design
People Activity r1 (Г1–Г3, В1; HANDOFF «Активность — кардиограмма за сутки») и SPEC-activity §6.3.

* ось времени — отметки сетки графиков (Claude Design «Сетка на графиках r1» 30.10): «Сутки» — круглые часы через
  4 ч (телефон 6), «7 дней» — полночь каждого дня (было «чт 22 · сб 24» — пятница пропадала), «30 дней» — понедельники;
* длительности «Графика» — часами без суток («на связи 277 ч 7 мин», ТЗ §6.3 «на связи 142 ч»); в строках устройств —
  без минут («на связи 11 ч», эталон Г1), иначе «на связи 13 ч 40 м…» резалось без подсказки;
* нажатие на линию выхода — окно выхода на «Графиках» (HANDOFF «Выходы (В1, В2)»), а не на его верху.

Функции app.js — в node (как tests/test_users_ui_lines.py); время — по UTC (часы браузера подменены), чтобы тест не
зависел от пояса машины (TZ=UTC: отметки сетки — по часам браузера)."""
import json
import os
import re
import subprocess
import tempfile
import unittest

from tests import jsparts
from tests.test_panel_f0 import HAS_NODE, STATIC, read

H = 3600
T0 = 1794317100           # вт 10.11.2026 13:25 UTC — пятиминутная метка не на круглом часе


def run(script):
    js = read("app.js")
    parts = [read("spark.js"), "const SPK = () => window.KaskadSpark; const LANG = \"ru\";", jsparts.const(js, "arr")]
    parts += [jsparts.function(js, n) for n in ("actPoints", "actAxis", "actDur")]
    src = """
const window = globalThis; window.requestAnimationFrame = () => 0;
const RU = { "fmt.d": "д", "fmt.h": "ч", "fmt.m": "мин", "fmt.s": "с", "pp.act.now": "сейчас" };
const t = (k) => RU[k] || k;
const pad = (n) => String(n).padStart(2, "0");
const DATES = { time: (d) => pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()), day: (d) => pad(d.getUTCDate()) + "." + pad(d.getUTCMonth() + 1) };
const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const wday = (d) => WD[d.getUTCDay()] + " " + d.getUTCDate();
const WDAY1 = { format: (d) => WD[d.getUTCDay()] };
function fmtDur(v) {
  let s = Math.max(0, Math.floor(Number(v)));
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  if (d) return d + " " + t("fmt.d") + (h ? " " + h + " " + t("fmt.h") : "");
  if (h) return h + " " + t("fmt.h") + (m ? " " + m + " " + t("fmt.m") : "");
  if (m) return m + " " + t("fmt.m");
  return Math.max(0, s) + " " + t("fmt.s");
}
%s
(async () => {
%s
})().catch((e) => { console.log(JSON.stringify({ crash: String(e && e.stack || e) })); });
""" % ("\n".join(parts), script)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, env=dict(os.environ, TZ="UTC"))
    finally:
        os.unlink(path)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode or not out:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


def series(step, n, t0=T0):
    return {"step": step, "n": n, "t0": t0, "rx": [1] * n, "tx": [1] * n, "on_s": [0] * n, "act_s": [0] * n, "cover": [10] * n}


@unittest.skipUnless(HAS_NODE, "нет node")
class AxisTest(unittest.TestCase):
    def axis(self, j, rng, narrow=False):
        return run("console.log(JSON.stringify(actAxis(actPoints(%s, %s), %s, %s)));" % (
            json.dumps(j), json.dumps(rng), json.dumps(rng), json.dumps(narrow)))

    def test_week_is_every_midnight(self):
        ax = self.axis(series(H, 168), "7d")
        self.assertEqual([a["t"] for a in ax], ["ср 11", "чт 12", "пт 13", "сб 14", "вс 15", "пн 16", "вт 17"])   # ни один день не пропал
        self.assertEqual(round(ax[0]["at"], 4), round((10 * H + 35 * 60) / (168 * H), 4))      # полночь, а не 13:25

    def test_week_on_a_phone_is_the_weekday(self):
        ax = self.axis(series(H, 168), "7d", True)                      # восемь «20 Tue» не помещаются в 390
        self.assertEqual([a["t"] for a in ax], ["ср", "чт", "пт", "сб", "вс", "пн", "вт"])

    def test_day_marks_are_round_hours(self):
        ax = self.axis(series(300, 288), "1d")
        self.assertEqual([a["t"] for a in ax], ["16:00", "20:00", "00:00", "04:00", "08:00", "12:00"])   # было «13:25 · 17:25 …»
        self.assertEqual([round(a["at"], 4) for a in ax], [round((155 + 240 * i) / 1440, 4) for i in range(6)])
        self.assertEqual([a["t"] for a in self.axis(series(300, 288), "1d", True)], ["18:00", "00:00", "06:00", "12:00"])   # телефон — 6 ч

    def test_no_points_no_axis(self):
        self.assertEqual(run('console.log(JSON.stringify(["1d", "7d", "30d"].map((r) => actAxis([], r, true))));'), [[], [], []])

    def test_month_marks_are_mondays(self):
        ax = self.axis(series(H, 720, T0 - 13 * H - 25 * 60), "30d")    # от полуночи UTC: 30 полных суток
        self.assertEqual([a["t"] for a in ax], ["16 нояб.", "23 нояб.", "30 нояб.", "7 дек."])
        self.assertEqual([round(a["at"], 4) for a in ax], [round(i / 30, 4) for i in (6, 13, 20, 27)])   # начало суток


@unittest.skipUnless(HAS_NODE, "нет node")
class NodeDaysTest(unittest.TestCase):
    """Л3 (ACT-14, R-s1a5 Р-4/В-1): «30 дней» — сутки по поясу узла. Узел МСК (+3), часы браузера — UTC: час 22:00 UTC
    — уже следующие сутки узла. Метки days ответа (metrics.day_marks) — сутки и подписи оси и подсказки — день узла."""

    def test_days_of_the_node_not_of_the_browser(self):
        from kaskad import tz
        from kaskad.gw import metrics
        t0 = 1794268800 - 3 * H                           # 09.11.2026 21:00 UTC = 10.11 00:00 МСК
        j = series(H, 720, t0)
        j["rx"] = [0] * 720
        j["rx"][2] = 5                                    # 23:00 UTC 09.11 = 02:00 МСК 10.11 — первые сутки узла
        j["days"] = metrics.day_marks(t0, H, 720, tz.Zone.fixed("MSK", 3 * H))
        r = run("""const p = actPoints(%s, "30d");
console.log(JSON.stringify({ n: p.length, first: [p[0].ts, p[0].rx], ax: actAxis(p, "30d").map((a) => a.t) }));""" % json.dumps(j))
        self.assertEqual(r["n"], 30)                      # без правки: 31 сутки браузера, первые — 3 часа «09.11»
        self.assertEqual(r["first"], [t0, 5])             # 5 байт 02:00 МСК — в сутках 10.11 (без правки — в 09.11)
        self.assertEqual(r["ax"][0], "16 нояб.")                 # понедельник узла

    def test_without_marks_as_before(self):
        r = run("""const p = actPoints(%s, "30d"); console.log(JSON.stringify({ n: p.length, dd: p.some((x) => x.dd) }));"""
                % json.dumps(series(H, 720, T0 - 13 * H - 25 * 60)))
        self.assertEqual(r, {"n": 30, "dd": False})


@unittest.skipUnless(HAS_NODE, "нет node")
class DurTest(unittest.TestCase):
    def test_hours_without_days(self):
        r = run("""console.log(JSON.stringify([actDur(277 * 3600 + 7 * 60), actDur(277 * 3600 + 7 * 60, true), actDur(17 * 86400 + 15 * 3600),
  actDur(13 * 3600 + 40 * 60, true), actDur(40 * 60), actDur(40 * 60, true), actDur(3600, true), actDur(45), actDur(null)]));""")
        self.assertEqual(r, ["277 ч 7 мин", "277 ч", "423 ч", "13 ч", "40 мин", "40 мин", "1 ч", "45 с", "0 с"])


class StaticTest(unittest.TestCase):
    """Где «График» берёт длительности, куда ведёт линия выхода."""

    def body(self, src, name):
        return jsparts.function(src, name)

    def test_graph_durations_are_hours(self):
        g = self.body(read("app.js"), "openActGraph")
        self.assertNotIn("fmtDur(", g)                                   # «17 д 15 ч» — не по эталону
        self.assertIn('t("pp.act.onFor", { d: actDur(sum("on")) })', g)
        self.assertIn('t("pp.act.actFor", { d: actDur(sum("act")) })', g)
        self.assertIn('t("pp.act.onFor", { d: actDur(x.on, true) })', g)  # строка устройства — без минут (Г1)
        self.assertNotIn("fmtDur(", self.body(read("app.js"), "actPointTip"))

    def test_the_exit_window_has_no_activity_line(self):
        """Л3 (R-s1a5 В-2, решение 28.10): линия активности выхода — только в списке «Выходы» (эталон В1, В2); в окне
        выхода её нет — эталон её не рисует, там «Графики» (трафик за 1/7/30/90 дней). Окно — часть exits-groups.js:
        ни линии gx-spark, ни сводки GET /api/gw/exits/activity; ядро не передаёт ей свою линию (exSpark, exaOf)."""
        eg = read(os.path.join("pages", "exits-groups.js"))
        for word in ("gx-spark", "KaskadSpark", "exits/activity", "exSpark", "exaOf", "exAct"):
            self.assertNotIn(word, eg, word)
        self.assertEqual(self.body(read("app.js"), "openExitDetail").count("pt.openExit(id, at)"), 1)
        self.assertIn("chartsBlock(id, go ? toCharts : null)", self.body(eg, "openExit"))   # «Графики» — в окне

    def test_exit_line_opens_the_charts(self):
        js = read("app.js")
        self.assertTrue(re.search(r'open: \(\) => openExitDetail\(r\.id, "charts"\)', js), "линия выхода — не на «Графики»")
        det = self.body(js, "openExitDetail")
        self.assertIn("pt.openExit(id, at)", det)
        eg = read(os.path.join("pages", "exits-groups.js"))
        op = self.body(eg, "openExit")
        self.assertRegex(op, r"function openExit\(id, at\)")
        self.assertIn('const go = at === "charts" ? { on: true } : null;', op)
        self.assertIn('charts.scrollIntoView({ block: "start" })', op)
        self.assertIn("chartsBlock(id, go ? toCharts : null)", op)              # и ещё раз — когда графики нарисованы
        cb = self.body(eg, "chartsBlock")
        self.assertRegex(cb, r"function chartsBlock\(id, shown\)")
        self.assertIn("paint(j); once();", cb)

    def test_layout_detector_opens_the_graph(self):
        """Окно и лист «Графика» на 7 и 30 дней — в детекторе вёрстки, с правилом grfit (восемь «20 Tue» на 390
        распирали лист — прокрутка внутри листа не hscroll документа, детектор её не видел)."""
        with open(os.path.join(os.path.dirname(__file__), "..", "tools", "k3_layout_check.mjs"), encoding="utf-8") as f:
            tool = f.read()
        self.assertIn("out.push(['grfit', name(e), 'шире себя '", tool)
        self.assertIn("out.push(['grfit', name(e), 'подписи оси '", tool)
        for v in ('"people-graph7"', '"people-graph30"', "graph=${rng}", 'GWIDTHS = WIDTHS.filter((x) => x >= 700)'):
            self.assertIn(v, tool)
        # REV-s1fin С-2: на 768–1024 «Пользователи» — список планшета, «⋯» у строки списка, а не у строки таблицы
        # (07d556d4 сузил ширины до 1100 — на планшете окно не проверялось вовсе)
        for v in ('.kk-plist .kk-pg")].find((x) => x.querySelector(".kk-prow__exp"))', '.kk-prow > .kk-prow__more',
                  "querySelector('.gx-xt__g, .kk-plist .kk-pg')"):
            self.assertIn(v, tool)


if __name__ == "__main__":
    unittest.main()
