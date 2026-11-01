"""3.3.7.10, «Пользователи и выходы: автообновление, подписи графиков» (владелец 24.10). Функции app.js — в node:

* живые метки сборщика доходят до модели (normLive: activity, metrics) — раньше activity терялась, и сводка
  «Пользователей» не перечитывалась;
* открытое окно сборщика прибавляется к сводке (actLive) и к ряду «Графика» (actSeriesLive) — только когда оно идёт
  сразу за записанным; окно в новых 30 минутах — сдвиг линии; пропуск между ними — ничего не выдумывается;
* линия «Выходов» из одного ответа (exaOf);
* подписи «Крупно»: линия от начала имени до колонки объёма, объём — колонкой фиксированной ширины (people.css)."""
import json
import os
import re
import subprocess
import tempfile
import unittest

from tests import jsparts
from tests.test_panel_f0 import HAS_NODE, STATIC, read


def run(script, data=None):
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in ("known", "isObj", "arr", "ACT0", "ACT_MEMO")]    # ACT0 — with ACTND
    parts += [jsparts.function(js, n) for n in ("normLive", "normActivity", "actLive", "actSeriesLive", "exaOpen", "exaOf", "mergePatch")]
    src = """
const ST = { live: {}, off: false };
const LV = () => ST.live, liveOff = () => ST.off;
const toMap = (v, keyOf) => { if (Array.isArray(v)) { const o = {}; v.forEach((x) => { if (x && keyOf(x)) o[keyOf(x)] = x; }); return o; } return v && typeof v === "object" ? v : undefined; };
const EXA = { j: null, calc: Object.create(null) }, EXA_STEP = 1800;
%s
const DATA = %s;
(async () => {
%s
})().catch((e) => { console.log(JSON.stringify({ crash: String(e && e.stack || e) })); });
""" % ("\n".join(parts), json.dumps(data or {}), script)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = r.stdout.decode("utf-8").strip().splitlines()
    if r.returncode or not out:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


B = 1791190800                  # a 30-minute mark: the summary of «Пользователи» ends here or 5 min later
SUM = {"ok": True, "activity": True, "step": 1800, "n": 48, "since": 1, "cover": [10] * 48,
       "devices": {"d-1": {"on": [0] * 47 + [5], "kb": [0] * 47 + [100], "rx": 1000, "tx": 9000, "on_s": 600, "act_s": 60,
                           "last": B}}}


def summary(anchor):
    """The summary as the server gives it with the end ``anchor`` (metrics.dev_summary)."""
    t0 = (anchor - 1) // 1800 * 1800 - 47 * 1800
    return dict(SUM, t0=t0, now=anchor)


@unittest.skipUnless(HAS_NODE, "нет node")
class LiveMarksTest(unittest.TestCase):
    def test_normlive_keeps_the_marks(self):
        act = {"ts": B, "w": B + 300, "at": B + 320, "secs": 20, "cur": {"d-1": [20, 20, 5, 5]}}
        out = run("""
const full = normLive(Object.assign({ ts: 1, devices: {} }, DATA.full));
const snap = normLive({ live: { ts: 1, fresh: true }, ts: 1, devices: {}, activity: DATA.full.activity, metrics: DATA.full.metrics });
console.log(JSON.stringify({ full: [full.activity, full.metrics], snap: [snap.activity, snap.metrics] }));
""", {"full": {"activity": act, "metrics": {"ts": B}}})
        self.assertEqual(out["full"], [act, {"ts": B}])
        self.assertEqual(out["snap"], [act, {"ts": B}])


@unittest.skipUnless(HAS_NODE, "нет node")
class ActLiveTest(unittest.TestCase):
    def merge(self, anchor, act, off=False):
        return run("""
ST.live = { activity: DATA.act }; ST.off = DATA.off;
const A = normActivity(DATA.sum), M = actLive(A), d = M.devices["d-1"] || null, n = M.devices["d-2"] || null;
console.log(JSON.stringify({ same: M === A, again: actLive(A) === M, t0: [A.t0, M.t0], now: M.now, kb: d && [d.kb[46], d.kb[47]],
  on: d && d.on[47], cover: M.cover[47], rx: d && d.rx, tx: d && d.tx, on_s: d && d.on_s, last: d && d.last, n: n && [n.kb[47], n.rx, n.has] }));
""", {"sum": summary(anchor), "act": act, "off": off})

    def test_the_open_window_right_after_the_summary_is_added(self):
        a = B + 300                                            # written up to 16:05, the window 16:05–16:10 is open
        out = self.merge(a, {"ts": B, "w": a, "at": a + 150, "secs": 150,
                             "cur": {"d-1": [150, 30, 51200, 51200], "d-2": [150, 0, 1024, 0]}})
        self.assertFalse(out["same"])
        self.assertTrue(out["again"])                          # the same summary and live — the same answer (memo)
        self.assertEqual(out["t0"][0], out["t0"][1])
        self.assertEqual(out["now"], a + 150)
        self.assertEqual(out["kb"], [0, 200])                  # 100 KiB written + 100 KiB of the open window
        self.assertEqual((out["rx"], out["tx"], out["on_s"], out["last"]), (1000 + 51200, 9000 + 51200, 750, a + 150))
        # «на связи» of the window: 5/10 of 300 s written + 150 s open = 300 of 450 s -> 7/10
        self.assertEqual((out["on"], out["cover"]), (7, 10))
        self.assertEqual(out["n"], [1, 1024, True])            # a device the summary had not — from the open window

    def test_the_open_window_in_new_30_minutes_shifts_the_line(self):
        out = self.merge(B, {"ts": B - 300, "w": B, "at": B + 60, "secs": 60, "cur": {"d-1": [60, 60, 1024 * 300, 0]}})
        self.assertEqual(out["t0"][1] - out["t0"][0], 1800)
        self.assertEqual(out["kb"], [100, 300])                 # the old last window moved left, the open one is the last
        self.assertEqual(out["rx"], 1000 + 1024 * 300)          # the totals: the summary + the open window

    def test_a_gap_or_no_live_shows_what_there_is(self):
        gap = self.merge(B + 300, {"ts": B + 300, "w": B + 600, "at": B + 650, "secs": 50, "cur": {"d-1": [50, 50, 9999, 0]}})
        self.assertTrue(gap["same"])                            # a window written after the summary: it is read again
        off = self.merge(B + 300, {"ts": B, "w": B + 300, "at": B + 320, "secs": 20, "cur": {"d-1": [20, 20, 5, 5]}}, off=True)
        self.assertTrue(off["same"])                            # the collector is silent: nothing is added
        bad = self.merge(B + 300, {"ts": B, "w": B + 300, "at": B + 10, "secs": 0, "cur": {}})
        self.assertTrue(bad["same"])                            # the last round before the window: not this window


@unittest.skipUnless(HAS_NODE, "нет node")
class SeriesLiveTest(unittest.TestCase):
    def test_a_day_shifts_by_the_open_window(self):
        anchor = B + 300
        n, step = 288, 300
        t0 = (anchor - 1) // step * step - (n - 1) * step
        j = {"ok": True, "activity": True, "step": step, "n": n, "t0": t0, "now": anchor, "since": 1,
             "rx": [0] * (n - 1) + [7], "tx": [0] * n, "on_s": [0] * n, "act_s": [0] * n, "cover": [10] * n,
             "devices": [{"id": "d-1", "rx": 7, "tx": 0, "on_s": 0, "act_s": 0}]}
        out = run("""
ST.live = { activity: DATA.act };
const M = actSeriesLive(DATA.j, ["d-1", "d-2"]), S = actSeriesLive(DATA.j, ["d-9"]);
ST.live = { activity: Object.assign({}, DATA.act, { w: DATA.act.w + 300 }) };
const G = actSeriesLive(DATA.j, ["d-1"]);
console.log(JSON.stringify({ t0: M.t0 - DATA.j.t0, len: M.rx.length, last: [M.rx[287], M.rx[286], M.tx[287], M.on_s[287], M.cover[287]],
  devs: M.devices, untouched: DATA.j.rx[287], other: S.rx[287], gap: G === DATA.j }));
""", {"j": j, "act": {"ts": B, "w": anchor, "at": anchor + 100, "secs": 100,
                      "cur": {"d-1": [100, 20, 500, 600], "d-2": [80, 0, 10, 0]}}})
        self.assertEqual((out["t0"], out["len"]), (300, 288))
        self.assertEqual(out["last"], [510, 7, 600, 100, 10])    # bytes summed, seconds the most of the devices
        self.assertEqual(sorted(out["devs"], key=lambda x: x["id"]), [
            {"id": "d-1", "rx": 507, "tx": 600, "on_s": 100, "act_s": 20}, {"id": "d-2", "rx": 10, "tx": 0, "on_s": 80, "act_s": 0}])
        self.assertEqual((out["untouched"], out["other"], out["gap"]), (7, 0, True))


@unittest.skipUnless(HAS_NODE, "нет node")
class ExitLineTest(unittest.TestCase):
    def test_one_answer_for_all_exits(self):
        end = B
        out = run("""
EXA.j = DATA.j;
const a = exaOf("e-0123456789"), none = exaOf("e-ffffffffff");
EXA.j = null;
console.log(JSON.stringify({ kb: a.kb[47], nd: [a.nd[47], a.nd[0]], st: a.st[47], tot: [a.rx, a.tx, a.off, a.deg], frac: a.frac,
  t: [a.t0, a.end, a.now], none: [none.nd.every(Boolean), none.rx], cached: exaOf("e-0123456789") === null }));
""", {"j": {"charts": True, "step": 1800, "n": 48, "t0": end - 47 * 1800, "now": end + 900,
            "exits": {"e-0123456789": {"kb": [0] * 47 + [2048], "nd": [1] + [0] * 47, "st": [0] * 47 + [2],
                                       "rx": 5, "tx": 6, "off": 0, "deg": 60}}}})
        self.assertEqual((out["kb"], out["nd"], out["st"]), (2048, [False, True], 2))
        self.assertEqual(out["tot"], [5, 6, 0, 60])
        self.assertEqual((out["frac"], out["t"]), (0.5, [B - 47 * 1800, B, B + 900]))
        self.assertEqual(out["none"], [True, 0])               # an exit without minutes in the day: «нет данных»
        self.assertTrue(out["cached"])                          # no answer — no line (not the old one)


    def test_the_open_minute_goes_onto_its_answer_only(self):
        """3.3.7.11: live.json → metrics.cur — the bytes after the point ts — into the window where ``at`` is and into
        the day; only onto the answer that ends with that point (now = ts + 60), not with the collector silent."""
        end = B
        out = run("""
EXA.j = DATA.j;
const pick = (a) => [a.kb[47], a.nd[47], a.rx, a.tx, a.now, a.frac];
ST.live = { metrics: DATA.m };
const a = pick(exaOf("e-0123456789")), none = pick(exaOf("e-ffffffffff")), fresh = pick(exaOf("e-aaaaaaaaaa"));
ST.live = { metrics: Object.assign({}, DATA.m, { ts: DATA.m.ts - 60 }) };
const other = pick(exaOf("e-0123456789"));
ST.live = { metrics: DATA.m }; ST.off = true;
const off = pick(exaOf("e-0123456789"));
ST.off = false; ST.live = { metrics: { ts: DATA.m.ts } };
const old = pick(exaOf("e-0123456789"));
console.log(JSON.stringify({ a, none, fresh, other, off, old }));
""", {"j": {"charts": True, "step": 1800, "n": 48, "t0": end - 47 * 1800, "now": end + 900,
            "exits": {"e-0123456789": {"kb": [0] * 47 + [2048], "nd": [1] + [0] * 47, "st": [0] * 47 + [2],
                                       "rx": 5, "tx": 6, "off": 0, "deg": 60}}},
      "m": {"ts": end + 840, "at": end + 1260, "cur": {"e-0123456789": [10240, 20480], "e-aaaaaaaaaa": [0, 0]}}})
        self.assertEqual(out["a"], [2048 + 30, False, 5 + 10240, 6 + 20480, end + 1260, 0.7])
        self.assertEqual(out["none"], [0, True, 0, 0, end + 900, 0.5])      # not in cur: nothing invented
        self.assertEqual(out["fresh"], [0, False, 0, 0, end + 1260, 0.7])   # read, zero bytes: data, not «нет данных»
        base = [2048, False, 5, 6, end + 900, 0.5]
        self.assertEqual(out["other"], base)                                # another point: the answer is re-read
        self.assertEqual(out["off"], base)                                  # the collector silent
        self.assertEqual(out["old"], base)                                  # a collector before 3.3.7.11: {ts} only

    def test_the_fallback_poll_sees_every_5_s(self):
        js = read("app.js")
        ms = int(re.search(r"const POLL_MS = (\d+);", js).group(1))
        self.assertLessEqual(ms, 2500)                                     # data ≤ 5 s + a poll ≤ 2,5 s: seen ≤ 5 s
        self.assertIn("T.slowTick % POLL_SLOW === 0", js)                  # «just in case» stays ≈ 30 s


class HoldTest(unittest.TestCase):
    def test_a_line_under_the_mouse_waits(self):
        # живые линии перестраиваются раз в круг сборщика: ряд с линией под мышью ждёт (подсказка окна не пропадает)
        js = read("app.js")
        self.assertIn(".gx-spark:hover", re.search(r"const HOLD_SEL = '([^']+)';", js).group(1))


class LineWidthTest(unittest.TestCase):
    """Стенд Н-1 (24.10): линия активности — фиксированной ширины по ТЗ (SPEC-activity §6.2, HANDOFF «Размеры по видам
    списка») на любой ширине: не растягивается, колонка «Активность» — ровно под линию и объём."""

    def test_css(self):
        css, sty = read("people.css"), read("styles.css")
        # Д2 вариант Б (владелец 28.10) сменил правило Н-1 у «Пользователей»: линия — на всю свободную ширину строки
        # (kk-sp1, колонка minmax(…, 1fr)), объёма рядом нет (№21). Растягивается только kk-sp1; прочие линии — прежние
        for f in ("people.css", "styles.css", "charts.css"):
            rules = re.findall(r"([^{}]*gx-spark[^{}]*)\{([^}]*)\}", read(f))
            bad = [sel.strip() for sel, body in rules if "kk-sp1" not in sel and re.search(r"flex: 1 1 auto|width: auto|min-width", body)]
            self.assertEqual(bad, [], f)
        self.assertIn(".gx-spark--lg { --spark-w: 120px; --spark-h: 20px; }", read("charts.css"))
        self.assertIn(".kk-act1 > .kk-sp1 { --spark-w: 100%; --spark-h: 24px; }", css)
        self.assertRegex(css, r"\.gx-xt--k1 > \* \{ --cols: 32px minmax\(7\.5rem, var\(--pnw\)\) minmax\(var\(--actm, 0px\), 1fr\) 4\.5rem ")
        self.assertNotIn("--spark-w: 140px", css)
        self.assertNotIn(".gx-xt--k1 .kk-act__v", css)
        self.assertRegex(css, r"\.kk-act__v \{ display: none; flex: none; width: var\(--act-vw\);[^}]*text-align: right")
        self.assertIn("calc(140px + 8px + var(--act-vw)) 52px; }", sty)
        self.assertIn(".gx-xt--exa .kk-act__v { display: block; }", sty)
        self.assertNotRegex(sty, r"minmax\((96|200)px, 1fr\)")          # у «Выходов» колонка линии свободную ширину не берёт
