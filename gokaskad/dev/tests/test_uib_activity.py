"""UI-B 23.10: активность людей и устройств — «кардиограмма» за сутки (spark.js, KaskadSpark), её данные на заглушке
разработчика (dev-mock, формат k3/handoff/SPEC-activity.md §5) и тексты pp.act.* (RU = EN).

Правила линии — Claude Design People Activity r1, HANDOFF «Активность — кардиограмма за сутки»: viewBox — пиксели
ячейки, 48 окон по 30 мин, высота — логарифм объёма окна в степени 1,7 от одной шкалы, нет данных — пунктир по нулю."""
import json
import math
import os
import re
import subprocess
import tempfile
import unittest

from tests.test_panel_f0 import HAS_NODE, STATIC, read
from tests.test_panel_js_contract import node as node_app


def strip_comments(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(^|[^:\"'\\])//[^\n]*", r"\1", js)


def spark(script):
    """Запустить ``script`` в node рядом с загруженным spark.js (без DOM: line и mono DOM не трогают)."""
    src = "globalThis.window = globalThis;\nrequire(%s);\nconst S = window.KaskadSpark;\n%s\n" % (
        json.dumps(os.path.join(STATIC, "spark.js")), script)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "spark_t.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(src)
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


def nums(path):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", path)]


class StaticTest(unittest.TestCase):
    def test_block_is_portable(self):
        """Переносимый блок (process/BLOCKS.md): ни фраз, ни зависимостей от app.js, DOM — только узлами."""
        js = strip_comments(read("spark.js"))
        self.assertNotRegex(js, r"[А-Яа-яЁё]")                           # тексты приходят параметрами
        for dep in (r"\bt\(", r"\bR\.", r"\bAPI\.", r"\bfmtBytes\b", r"\bKaskadXT\b", r"\bdocument\.write"):
            self.assertNotRegex(js, dep)
        self.assertNotRegex(js, r"innerHTML|outerHTML|insertAdjacentHTML|setAttribute\(\s*[\"']style")   # CSP
        self.assertIn("g.KaskadSpark = S;", js)
        self.assertIn("var NW = 48;", js)
        self.assertIn("Math.pow(Math.min(1, Math.log(kb / sc[0]) / L), 1.7)", js)    # логарифм, степень 1,7
        self.assertIn('viewBox: "0 0 " + W + " " + H', js)                            # viewBox — пиксели ячейки

    def test_sizes_of_the_line(self):
        """Размеры по HANDOFF: Средне 96 × 16, Крупно 120 × 20, Строка 48 × 12, лист телефона — во всю ширину × 32."""
        css = read("charts.css")
        self.assertIn("[data-hero] { --spark-h: 16px; --spark-w: 96px; }", css)
        self.assertIn(".gx-spark--lg { --spark-w: 120px; --spark-h: 20px; }", css)
        self.assertIn(".gx-spark--row { --spark-w: 48px; --spark-h: 12px;", css)
        self.assertIn(".gx-spark--sheet { --spark-w: 100%; --spark-h: 32px; }", css)
        self.assertRegex(css, r"\.gx-spark__nd \{[^}]*stroke-dasharray")          # нет данных — пунктир

    def test_api_is_the_spec(self):
        """Адреса — SPEC-activity §5, не выдуманные: сводка за сутки и ряд устройства или человека за период."""
        app = read("app.js")
        # 3.3.7.9: сводка с ETag — на 304 панель берёт прошлый ответ (SPEC-activity §5.1, «Сверка 24.10»)
        self.assertIn('sum: () => request("GET", "/api/gw/activity", undefined, { tag: ACT_TAG }),', app)
        self.assertIn('keepGet: () => request("GET", "/api/gw/activity/settings"),', app)
        self.assertIn('keepPut: (b) => request("PUT", "/api/gw/activity/settings", b),', app)
        self.assertIn('request("GET", "/api/gw/devices/" + enc(id) + "/activity" + qs({ range }))', app)
        self.assertIn('request("GET", "/api/gw/users/" + enc(id) + "/activity" + qs({ range }))', app)
        self.assertIn('defRes("activity", API.activity.sum, normActivity, { optional: true, need: "activity" });', app)
        self.assertIn('const ACT_RANGES = [["1d", ', app)
        # чужой ввод: id устройств с сервера — ключи карт без прототипа («__proto__» не подменит прототип)
        self.assertIn("const devices = Object.create(null);", app)
        self.assertIn("const per1 = Object.create(null);", app)

    def test_texts_ru_en(self):
        ru, en = read("i18n-ru.js"), read("i18n-en.js")
        kr = sorted(set(re.findall(r'"(pp\.act\.[A-Za-z]+)":', ru)))
        self.assertGreater(len(kr), 20)
        self.assertEqual(kr, sorted(set(re.findall(r'"(pp\.act\.[A-Za-z]+)":', en))))
        for k in set(re.findall(r'\bt\("(pp\.act\.[A-Za-z]+)"', read("app.js"))):
            self.assertIn(k, kr)
        vals = lambda s: "\n".join(v for k, v in re.findall(r'^\s*"(pp\.act\.[^"]+)": "(.*)",?$', s, re.M))
        # без жаргона: ни rx/tx, ни «спарклайн», ни «кардиограмма» в текстах для человека
        self.assertNotRegex(vals(ru), r"(?i)\b(rx|tx|spark\w*)\b|кардиограмм|спарклайн")
        self.assertNotRegex(vals(en), r"(?i)\b(rx|tx|spark\w*|sparkline|cardiogram)\b")

    def test_storage_switch(self):
        """3.3.7.9, кадр Н1: «Хранить историю активности» — настоящая настройка (GET/PUT /api/gw/activity/settings с ver,
        полоса сохранения, 409 — перекладка), пояснение под переключателем меняется сразу, занято и итоги за сутки;
        у сервера без маршрута (404) — заготовка «с версии …». Выключили — «Люди» перечитывают активность."""
        app, css = read("app.js"), read("styles.css")
        body = app[app.index("const stof = {"):app.index("function storageCard()") + 4000]      # форма части целиком
        for need in ("API.activity.keepGet()", "API.activity.keepPut({ ver: stof.ver, keep: stof.draft.keep })",
                     "window.Kit.saveBar(", "onConflict: () => storageLoad(true)", 'loadQuiet("activity")',
                     'class: "kk-swwarn", text: t(on ? "sto.keepOn" : "sto.keepOff")', "stof.missing) kids.push(storageSoon())"):
            self.assertIn(need, body, need)
        self.assertIn("e.status === 404) { stof.missing = true;", body)
        self.assertIn('const STO_WHEN = "3.3.7.9";', app)
        self.assertIn(".gx-sw__t > .kk-swwarn { color: var(--text-3); }", css)
        ru, en = read("i18n-ru.js"), read("i18n-en.js")
        keys = sorted(set(re.findall(r'\bt\((?:on \? )?"(sto\.[A-Za-z]+)"', app)) | set(re.findall(r'"(sto\.[A-Za-z]+)"', body)))
        self.assertGreater(len(keys), 12)
        for k in keys:
            self.assertIn('"%s":' % k, ru, k)
            self.assertIn('"%s":' % k, en, k)
        mock = read("dev-mock.js")
        self.assertIn('if (path === "/api/gw/activity/settings") {', mock)
        self.assertIn('path === "/api/gw/activity/settings" ||', mock)

    def test_cut_text_has_full_text_in_tip(self):
        """k3_layout_check 23.10: метка состояния в узкой колонке (и у имени на телефоне) не срезается краем —
        многоточие внутри метки, а в подсказке — полный текст метки; имя человека на телефоне — с подсказкой."""
        css, app = read("people.css"), read("app.js")
        self.assertRegex(css, r"\.gx-xt__st > \.gx-chip, \.kk-prow__t > \.gx-chip \{[^}]*overflow: hidden; text-overflow: ellipsis;")
        self.assertIn(".gx-xt__st > .gx-chip { flex-shrink: 1; min-width: 0; }", css)
        self.assertIn('const ppTip = (pr) => (pr.why ? pr.chip + ": " + pr.why : pr.chip);', app)
        # строка таблицы (01b) и строка телефона (3.3.7.15 Д1, П2: короче словом, как в таблице)
        self.assertEqual(app.count("stChip(pr.short || pr.chip, pr.rowS, pr.tone, ppTip(pr))"), 2)
        self.assertNotIn("tip: pr.why }", app)
        self.assertIn('el("span", { class: "kk-prow__n", text: u.name, title: u.name })', app)

    def test_wide_grid_is_the_coordinator_decision(self):
        """Д2 вариант Б (владелец 28.10) вместо 01b: имя постоянной ширины, линия — свободная ширина (1fr); с таблицы 1500
        свободное делится между линией (1 доля), «Протоколами» (0,6 — метки без «+N») и «Маршрутом» (0,5); «Протоколы»
        и «Маршрут» не по содержимому (--pw / --fw, «до 3 плашек» сняты); таблица не шире 2000."""
        css, xt, app = read("people.css"), read("xt.js"), read("app.js")
        self.assertIn(".gx-xt--k1 > * { --cols: 32px minmax(7.5rem, var(--pnw)) minmax(var(--actm, 0px), 1fr) 4.5rem var(--ppw, 6.5rem) var(--prw, 7.625rem) 92px; }", css)
        self.assertIn("@container xt (min-width: 1500px) {", css)
        self.assertIn(".gx-xt--k1 > * { --pnw: 15rem; --ppw: minmax(6.5rem, .6fr); --prw: minmax(7.625rem, .5fr); }", css)
        self.assertNotIn("--slot-max", css)
        # ширина по содержимому — именно --pw / --fw (xt.js); --pw1 / --nm1 — другое: нижний предел колонки в
        # is-cc-tight (Н-2, REV-it1-pol П-1), не выше постоянной ширины 6,5rem / 10rem, верх колонки — по-прежнему --ppw
        self.assertNotRegex(css, r"var\(--(?:pw|fw)\s*[,)]")
        self.assertIn("--tpw: max(0px, min(var(--pw1, 0px), 6.5rem,", css)
        self.assertIn("minmax(var(--tpw), var(--ppw, 6.5rem))", css)
        self.assertEqual(len(re.findall(r"var\(--pw1\b", css)), len(re.findall(r"min\(var\(--pw1, 0px\)", css)))
        self.assertIn("#part-people { max-width: 2000px; }", css)
        self.assertIn(".gx-xt__c.kk-nrow { flex-wrap: nowrap; }", css)
        self.assertIn('p.max = Math.max(0, parseInt(cs.getPropertyValue("--slot-max"), 10) || 0);', xt)   # блок умеет, «Люди» не просят
        self.assertIn('class: "gx-xt__c kk-nrow kk-rt", title: tip, "data-ck": "route"', app)
        self.assertIn("ro.observe(s);", read("spark.js"))                  # линия перерисовывается по своей ячейке

    def test_k6_k7_are_backlog(self):
        """Координатор 23.10: К6 / К7 вне эталона — в бэклог; вид всегда К1, переключателя вида нет."""
        app, css = read("app.js"), read("people.css")
        for gone in ("ppViewBtn", "peopleTable7", "personK7Row", "kaskad-people-view", "ppSecRow"):
            self.assertNotIn(gone, app)
        for gone in ("gx-xt--k7", ".gx-xt__sec", ".gx-dp", "kk-pp__view"):
            self.assertNotIn(gone, css)
        for lang in ("i18n-ru.js", "i18n-en.js"):
            self.assertNotRegex(read(lang), r'"pp\.(view|k7|sec)\.')


@unittest.skipUnless(HAS_NODE, "нет node")
class LineTest(unittest.TestCase):
    """KaskadSpark.line: точки — центры окон, конец — «сейчас» у правого края, шкала одна, нет данных — пунктир."""

    def test_scale_log_and_power(self):
        r = spark("""
const W = 96, H = 16, lo = 20, hi = 512000, mid = Math.sqrt(lo * hi);
const one = (kb) => { const a = new Array(48).fill(0); a[47] = kb; return S.line({ kb: a, nd: new Array(48).fill(false), frac: 1 }, W, H).cy; };
console.log(JSON.stringify({ zero: one(0), lo: one(lo), hi: one(hi), over: one(hi * 10), mid: one(mid), sx: S.line({ kb: [], nd: new Array(48).fill(false), frac: 0.5 }, W, H).sx }));
""")
        base, top = 16 - 0.75, 1
        self.assertEqual((r["zero"], r["lo"]), (base, base))            # нет трафика и ≤ нижней границы — на нуле
        self.assertEqual((r["hi"], r["over"]), (top, top))              # верх шкалы и выше — у верха
        want = base - (base - top) * math.pow(0.5, 1.7)                 # середина логарифма — 0,5^1,7 высоты
        self.assertAlmostEqual(r["mid"], want, delta=0.01)
        self.assertAlmostEqual(r["sx"], 96 / 47.5, places=6)           # sx = W / (47 + f), f — доля последнего окна

    def test_line_ends_now_and_stays_in_the_box(self):
        r = spark("""
const kb = Array.from({ length: 48 }, (_, i) => (i % 7 === 3 ? 400000 : i % 5 === 0 ? 3000 : 0));
const p = S.line({ kb, nd: new Array(48).fill(false), frac: 1 }, 120, 20);
console.log(JSON.stringify(p));
""")
        xs = nums(r["l"])[0::2]
        ys = nums(r["l"])[1::2]
        self.assertEqual(xs[0], 0)
        self.assertEqual(xs[-1], 120)                                   # последняя точка — «сейчас», x = W
        self.assertEqual(r["nd"], "")
        self.assertGreaterEqual(min(ys), 1 - 1e-6)                      # монотонная кубическая: не выше верха
        self.assertLessEqual(max(ys), 20 - 0.75 + 1e-6)                 # и не ниже нуля

    def test_no_data_is_a_dotted_gap(self):
        r = spark("""
const nd = new Array(48).fill(false); [20, 21, 22].forEach((i) => { nd[i] = true; });
const kb = new Array(48).fill(5000);
const gap = S.line({ kb, nd, frac: 1 }, 96, 16);
const none = S.line({ kb, nd: null, frac: 1 }, 96, 16);
console.log(JSON.stringify({ gap, none }));
""")
        sx = 96 / 48
        nd = nums(r["gap"]["nd"])
        self.assertEqual(len(nd), 3)                                    # один отрезок «M x y H x2»
        self.assertAlmostEqual(nd[0], 20 * sx + 1.5, places=2)          # зазор 1,5 px от конца линии
        self.assertAlmostEqual(nd[2], 23 * sx - 1.5, places=2)
        self.assertEqual(r["gap"]["l"].count("M"), 2)                   # линия — с разрывом
        self.assertEqual(r["none"]["l"], "")                            # данных нет вовсе — только пунктир
        self.assertEqual(nums(r["none"]["nd"])[-1], 96)


@unittest.skipUnless(HAS_NODE, "нет node")
class MockTest(unittest.TestCase):
    """Заглушка разработчика отдаёт активность в формате SPEC-activity §5 (условные данные — чтобы видеть линию)."""

    def run_mock(self, query, script):
        return node_app("""
globalThis.window = globalThis;
globalThis.location = new URL("http://127.0.0.1/index.html?mock=1&lat=0%s");
require(DATA.mock);
const call = async (p) => { const r = await window.KASKAD_MOCK.fetch(p, { method: "GET", headers: { "X-Kaskad-Admin": "1" } });
  return [r.status, await r.json()]; };
%s
process.exit(0);
""" % (query, script), {"mock": os.path.join(STATIC, "dev-mock.js")})

    def test_day_and_ranges(self):
        out = self.run_mock("", """
const [s, a] = await call("/api/gw/activity");
const ids = Object.keys(a.devices), d0 = a.devices[ids[0]];
const db = window.KASKAD_MOCK.db(), dev = db.devices[0], u = db.users.find((x) => db.devices.some((d) => d.user === x.id));
const r = {};
for (const g of ["1d", "7d", "30d"]) { const [st, j] = await call("/api/gw/devices/" + dev.id + "/activity?range=" + g); r[g] = [st, j.step, j.n, j.rx.length, j.cover.length]; }
const [us, uj] = await call("/api/gw/users/" + u.id + "/activity?range=7d");
console.log(JSON.stringify({ s, ok: a.ok, act: a.activity, step: a.step, n: a.n, cover: a.cover.length, ids: ids.length,
  keys: Object.keys(d0).sort(), on: d0.on.length, kb: d0.kb.length, r, us, udev: Array.isArray(uj.devices),
  bad: (await call("/api/gw/devices/" + dev.id + "/activity?range=2d"))[0], gone: (await call("/api/gw/devices/d-00000000/activity?range=1d"))[0] }));
""")
        self.assertEqual((out["s"], out["ok"], out["act"], out["step"], out["n"], out["cover"]), (200, True, True, 1800, 48, 48))
        self.assertGreater(out["ids"], 3)
        self.assertEqual(out["keys"], ["act_s", "kb", "last", "on", "on_s", "rx", "tx"])
        self.assertEqual((out["on"], out["kb"]), (48, 48))
        self.assertEqual(out["r"], {"1d": [200, 300, 288, 288, 288], "7d": [200, 3600, 168, 168, 168], "30d": [200, 3600, 720, 720, 720]})
        self.assertEqual((out["us"], out["udev"]), (200, True))
        self.assertEqual((out["bad"], out["gone"]), (400, 404))

    def test_off_and_none(self):
        off = self.run_mock("&act=off", 'console.log(JSON.stringify((await call("/api/gw/activity"))[1]));')
        self.assertEqual(off, {"ok": True, "activity": False, "why": "off"})
        none = self.run_mock("&act=none", 'console.log(JSON.stringify({ s: (await call("/api/gw/activity"))[0] }));')
        self.assertEqual(none["s"], 404)                                 # сервер до 3.3.7.9: адреса нет — «нет данных»


if __name__ == "__main__":
    unittest.main()
