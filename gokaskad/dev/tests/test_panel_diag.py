"""B15 (SPEC-node-score-schedule-share §2.6–2.7, §3.4–3.5, §11.1 п. 24–25, строка B15 §8): раздел «Диагностика» —
«Расписание» и «История» проверок (pages/diag.js). Контракт экрана в node и по исходнику:

* экран зовёт только API §2.6/§3.4 (K.API.host.bench → маршруты panel/host.py), своего fetch нет;
* пределы расписания — только сервер (§11.1 п. 25): черновик «Вс 23:30 + Пн 00:30» уходит в предпросмотр, ошибка
  `gap` показывается из ответа; подменённый ответ БЕЗ errors на то же — ошибки нет и «Сохранить» активна;
  «Пн 00:30 + Пн 23:30» — ошибок нет; errors берутся и из отказа 400 (B13);
* `tz` в PUT — ровно как в ответе GET (и null; REV-ns-b13);
* барабан 24 ч даёт «HH:MM» 00:00–23:59, в RU и EN одинаково; `type="time"` в статике нет;
* радио и переключатели без номеров (D-072); в детали проверки нет «Поделиться» (§11.1 п. 24);
* метки истории: резкое падение — метка и фильтр «С проблемами» (REV-design-2026-10-19-3 Н3-З4); «кто запустил» —
  семь подписей §3.5, `panel` — «из панели»;
* «от обычного» (NS-D30): число — из rel сервера; нет числа — «N из 3» только пока usual_n меньше нужного, иначе «—»;
  разница сравнения — со знаком «+» / «−» (NS-D33); поясов по порогам в панели нет (NS-D34)."""
import json
import os
import re
import unittest

from tests import jsparts
from tests.test_panel_kit_s1 import HAS_NODE, STATIC, read, run_node

ROUTES_B = {("GET", "/api/host/bench/schedule"), ("POST", "/api/host/bench/schedule/preview"),
            ("PUT", "/api/host/bench/schedule"), ("GET", "/api/host/bench"), ("GET", "/api/host/bench/compare"),
            ("GET", "/api/host/bench/export"), ("GET", "/api/host/bench/"),
            ("POST", "/api/host/bench"),                     # 6C «Проверить сервер» (П4-а): K.API.host.bench.start
            ("GET", "/api/host/bench/price")}                # его окно цены — чтением (T-3377 Н-15): K.API.host.bench.price
PURE = ("DAYS", "pad2", "hhmm", "FIELD")
FUNCS = ("wheelPick", "wheelStep", "timeParts", "daysKind", "entryOut", "bodyOf", "previewOf", "sheetErrs", "runFlags",
         "runFilter", "relOf", "signOf")


def pure_scope():
    src = read("pages/diag.js")
    return "\n".join([jsparts.const(src, n) for n in PURE] + [jsparts.function(src, n) for n in FUNCS])


def strip_comments(js):
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|\s//\s.*$", "", js)


class SourceTest(unittest.TestCase):
    def test_only_the_routes_of_the_schedule_and_the_history(self):
        """The screen talks to the server only through K.API.host.bench of app.js, whose paths are §2.6/§3.4."""
        js = strip_comments(read("pages/diag.js"))
        for bad in ("fetch(", "XMLHttpRequest", "request(", "/api/", "EventSource"):
            self.assertNotIn(bad, js, bad)
        used = set(re.findall(r"API\(\)\.(\w+)\(", js))
        self.assertEqual(used, {"schedule", "preview", "save", "runs", "run", "compare", "exportFile"})
        app = read("app.js")
        block = app[app.index("      bench: {"):app.index("      },", app.index("      bench: {"))]
        calls = set((m, p) for m, p in re.findall(r'request\("(GET|POST|PUT|DELETE)", "([^"]+)"', block))
        self.assertEqual(calls, ROUTES_B)

    def test_no_rules_of_the_schedule_in_the_panel(self):
        """§11.1 п. 25: no gap, no count per day, no «full per day» in JS — the limits are only shown, the codes only
        say which field an error of the server belongs to."""
        js = strip_comments(read("pages/diag.js"))
        codes = js.replace('full_per_day: "type"', "").replace('per_day: "days"', "")          # FIELD: код -> поле
        codes = codes.replace('full_per_day: "dg.offLimit"', "").replace('per_day: "dg.offLimit"', "")   # OFF_WHY: код -> текст
        for bad in ("gap_min", "full_per_day:", "per_day:", "1440", "120"):
            self.assertNotIn(bad, codes, bad)
        self.assertNotIn("limits.total", js)
        self.assertNotIn("limits.per_day", js)
        self.assertNotIn("limits.full_per_day", js)

    def test_no_input_type_time_in_the_screens(self):
        """§2.7: the time of an entry — the own wheel of 24 h; ``type="time"`` follows the browser's language (AM/PM in
        en-US). The section and its dictionaries and the mock have none (exits-groups.js of T8 is another screen)."""
        for name in ("pages/diag.js", "pages/diag-i18n-ru.js", "pages/diag-i18n-en.js", "dev-mock-diag.js", "index.html"):
            self.assertIsNone(re.search(r'type\s*[:=]\s*["\']time["\']', read(name)), name)

    def test_no_share_in_the_detail_of_a_check(self):
        """§11.1 п. 24: in v1 only the current state is shared (the «Сейчас» screen), never an old check. C10: the live
        button «Поделиться» is only at the score of «Сейчас» (scoreParts, круг 1 Т1) and opens the window (openShare -> pages/diag-share.js,
        also by #overview/now?open=share of the bot) — no history row, detail, compare or list has it; the data of the
        window — only GET /api/host/share through K.API.host.share (app.js), no fetch of its own."""
        src = strip_comments(read("pages/diag.js"))           # шапка файла ссылается на SPEC-…-schedule-share.md
        head = jsparts.function(src, "scoreParts")
        self.assertRegex(head, r'U\.btn\(t\("dg\.share"\), "tertiary", \(\) => openShare\(\), \{ md: true, icon: "arrow-up-from-line", tip: t\("dg\.shareTip"\)')
        self.assertNotIn("SHARE_WHEN", src)
        opener = jsparts.function(src, "openShare")
        self.assertIn('K.loadScript("pages/diag-share.js")', opener)
        rest = src.replace(head, "").replace(opener, "").replace("openShare: () => openShare(),", "")
        self.assertIsNone(re.search(r"share|Поделит", rest, re.I))
        for name in ("pages/diag.js", "pages/diag-i18n-ru.js", "pages/diag-i18n-en.js", "pages/diag-share.js"):
            self.assertNotIn("/api/host/share", strip_comments(read(name)), name)
        app = strip_comments(read("app.js"))
        self.assertEqual(re.findall(r".*/api/host/share.*", app), ['      share: (q) => request("GET", "/api/host/share" + qs(q)),'])
        win = strip_comments(read("pages/diag-share.js"))
        self.assertEqual(re.findall(r"K\.API\.[\w.]+\(", win), ["K.API.host.share("])
        for no in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "/api/", "http:", "https:", "Worker("):
            self.assertNotIn(no, win, no)
        for name in ("pages/diag-i18n-ru.js", "pages/diag-i18n-en.js"):
            keys = re.findall(r'^\s*"(dg\.[^"]+)":\s*"(?:[^"\\]|\\.)*(?:share|Share|Поделит)', read(name), re.M)
            self.assertEqual([k for k in keys if k != "dg.share" and not k.startswith("dg.sh.")], [], name)
            self.assertNotIn('"dg.shareSoon"', read(name), name)

    def test_radios_and_switches_have_no_numbers(self):
        """D-072: in the panel a choice is a radio or a switch, never «1) … 2) …»."""
        for name in ("pages/diag-i18n-ru.js", "pages/diag-i18n-en.js"):
            vals = re.findall(r'^\s*"dg\.[^"]+":\s*"((?:[^"\\]|\\.)*)"', read(name), re.M)
            self.assertGreater(len(vals), 150, name)
            self.assertEqual([v for v in vals if re.match(r"\s*\d+\)", v) or re.search(r"\s\d\)\s", v)], [], name)
        js = read("pages/diag.js")
        self.assertIn('role: "radio"', js)
        self.assertIn('role: "radiogroup"', js)

    def test_the_section_and_its_texts(self):
        """The section is in the inventory with its own lazy file and dictionaries; both dictionaries have the same
        keys. nav2 r1 (владелец 23.10): своего пункта меню нет — части «Сейчас · История · Расписание» в «Обзоре» группой
        «Проверка сервера»; запись описи осталась (moved: "overview") — ей грузится pages/diag.js."""
        app = read("app.js")
        nav = app[app.index("  const NAV = ["):app.index("  const NAV_SOON")]
        self.assertNotIn('id: "diag"', nav)
        self.assertIn('{ id: "diag", label: "nav.diag", lazy: "pages/diag.js", i18n: true, moved: "overview",', app)
        # круг 1 Т1: «Расписание» — в «Настройки › Проверка сервера», в «Обзоре» — «Сейчас · История»
        self.assertIn('["check", "n2.gCheck", [["now", "nav.diagNow", "gauge"], ["history", "nav.diagHistory", "clock"]]]', app)
        self.assertIn('"stethoscope": [E, ', read("icons.js"))
        keys = [re.findall(r'^\s*"(dg\.[^"]+)":', read("pages/diag-i18n-%s.js" % lang), re.M) for lang in ("ru", "en")]
        self.assertEqual(keys[0], keys[1])
        self.assertIn('loadScript("dev-mock-diag.js")', app)

    @unittest.skipUnless(HAS_NODE, "no node")
    def test_old_addresses_lead_to_the_overview(self):
        """nav2 r1: старые адреса #diag… (закладки, ссылки уведомлений и бота) ведут в «Обзор › Проверка сервера» — та же
        часть (tab), день истории (focus) — с ним же; адрес заменяется без новой записи истории (canonHash); без части —
        «Сейчас», день без части — «История». Адрес «Обзора» не трогается."""
        from tests.test_panel_f0 import nav_scope, window_with, run_node as f0_run
        app = read("app.js")
        out = f0_run(window_with("ru", []) + """
const location = { hash: "" }; const REPL = [];
const history = { replaceState: (s, t, h) => { REPL.push(h); location.hash = h; } };
const t = (k) => k;
%s
%s
const go = (h) => { location.hash = h; REPL.length = 0; canonHash(); const n = navState(); return [location.hash, n.page, n.tab, n.focus, REPL.length]; };
console.log(JSON.stringify({ bare: go("#diag"), hist: go("#diag?tab=history&focus=2026-10-20"), day: go("#diag?focus=2026-10-20"),
  sched: go("#diag?tab=schedule"), slash: go("#diag/history"), item: go("#diag?focus=diag.schedule"), bad: go("#diag?tab=evil"),
  ov: go("#overview?tab=history"), ovSched: go("#overview?tab=schedule") }));
""" % (nav_scope(), jsparts.function(app, "canonHash")))
        self.assertEqual(out["bare"], ["#overview?tab=now", "overview", "now", None, 1])
        self.assertEqual(out["hist"], ["#overview?tab=history&focus=2026-10-20", "overview", "history", "2026-10-20", 1])
        self.assertEqual(out["day"], ["#overview?tab=history&focus=2026-10-20", "overview", "history", "2026-10-20", 1])
        # круг 1 Т1: расписание — «Настройки › Проверка сервера»; старые адреса ведут туда (и «Обзора» тоже)
        to = ["#settings?tab=health&focus=settings.health", "settings", "health", "settings.health", 1]
        self.assertEqual(out["sched"], to)
        self.assertEqual(out["ovSched"], to)
        self.assertEqual(out["slash"], ["#overview?tab=history", "overview", "history", None, 1])
        self.assertEqual(out["item"], to)
        self.assertEqual(out["bad"], ["#overview?tab=now", "overview", "now", None, 1])
        self.assertEqual(out["ov"], ["#overview?tab=history", "overview", "history", None, 0])

    def test_who_started_it(self):
        """§3.5: seven labels of ``by``; ``panel`` — «из панели» (REV-design-2026-10-19-2 Н2-П5); ``auto`` — «повтор
        после резкого падения» (SS §2.8 п. 7, REV-ns-b15 З-5) — the same words as kaskadctl (host_sched.by.auto)."""
        ru = read("pages/diag-i18n-ru.js")
        want = {"schedule": "по расписанию", "panel": "из панели", "bot": "из бота", "cli": "из командной строки",
                "mcp": "через ИИ-помощника", "ssh": "из меню сервера", "auto": "повтор после резкого падения"}
        for by, text in want.items():
            self.assertIn('"dg.by.%s": "%s"' % (by, text), ru)
        cat = os.path.join(os.path.dirname(STATIC), "..", "i18n", "catalog")
        for lang in ("ru", "en"):
            with open(os.path.join(cat, lang, "host_sched.json"), encoding="utf-8") as f:
                cli = json.load(f)
            web = read("pages/diag-i18n-%s.js" % lang)
            self.assertTrue('"dg.by.auto": %s' % json.dumps(cli["host_sched.by.auto"], ensure_ascii=False) in web, lang)

    def test_the_proposal_says_both_in_one_quiet_hour(self):
        """REV-design-2026-10-19-2 Н2-П3: the two entries of the proposal are in the same quiet hour, and the line
        «обе — только когда сервер свободен»; the times come from the server's ``propose``."""
        ru, js = read("pages/diag-i18n-ru.js"), read("pages/diag.js")
        self.assertIn('"dg.propQuiet": "Обе — только когда сервер свободен."', ru)
        self.assertIn("time: q.time", js)
        self.assertIn("time: f.time", js)
        self.assertIn("doc.propose", js)


@unittest.skipUnless(HAS_NODE, "no node")
class ModelTest(unittest.TestCase):
    def run_js(self, code):
        return run_node(pure_scope() + "\n" + code)

    def test_the_wheel_gives_hh_mm_the_same_in_ru_and_en(self):
        out = self.run_js(r"""
const all = [];
for (let h = 0; h < 24; h += 1) for (let m = 0; m < 60; m += 1) all.push(hhmm(h, m));
const picks = [wheelPick(0, 40, 24), wheelPick(19, 40, 24), wheelPick(21, 40, 24), wheelPick(920, 40, 24), wheelPick(5000, 40, 60), wheelPick(-40, 40, 60)];
const steps = [wheelStep(0, "ArrowUp", 24), wheelStep(23, "ArrowDown", 24), wheelStep(10, "PageDown", 60), wheelStep(3, "Home", 60), wheelStep(3, "End", 60), wheelStep(3, "a", 60)];
console.log(JSON.stringify({ n: all.length, first: all[0], last: all[all.length - 1], bad: all.filter((x) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(x)),
  uniq: new Set(all).size, picks, steps, parts: [timeParts("23:59"), timeParts("7:5"), timeParts(null)] }));
""")
        self.assertEqual(out["n"], 1440)
        self.assertEqual(out["uniq"], 1440)
        self.assertEqual((out["first"], out["last"], out["bad"]), ("00:00", "23:59", []))
        self.assertEqual(out["picks"], [0, 0, 1, 23, 59, 0])
        self.assertEqual(out["steps"], [0, 23, 15, 0, 59, None])
        self.assertEqual(out["parts"], [[23, 59], [4, 0], [4, 0]])
        # the language never enters the wheel: no K.lang / Intl in hhmm, pad2, wheelPick, wheelStep
        scope = pure_scope()
        for name in ("hhmm", "pad2", "wheelPick", "wheelStep"):
            part = jsparts.const(read("pages/diag.js"), name) if name in PURE else jsparts.function(read("pages/diag.js"), name)
            self.assertNotIn("Intl", part)
            self.assertNotIn("lang", part)
        self.assertIn("hhmm", scope)

    def test_errors_only_from_the_answer_of_the_preview(self):
        """«Вс 23:30 + Пн 00:30»: the body of the preview holds both entries; the server's ``gap`` is shown at the
        time of the draft and «Сохранить» is off; the same draft with an answer WITHOUT errors — nothing shown, «Сохранить»
        on (no rule of its own); «Пн 00:30 + Пн 23:30» with the server's empty errors — nothing; a 400 refusal
        (ApiError with the body) gives its errors too."""
        out = self.run_js(r"""
const doc = { retry: true, tz: "Europe/Moscow", etag: "\"00000000000000000001\"" };
const list = [{ id: "a1b2c3", time: "23:30", mode: "quick", days: [7], on: true, quiet: true, monthly: false, since: 1, off_why: null },
  { time: "00:30", mode: "quick", days: [1], on: true, quiet: true, monthly: false }];
const body = bodyOf(list, doc);
const gapText = "Проверки в 23:30 и 00:30 — через 60 мин, нужно не меньше 2 ч.";
const gap = { ok: true, schedule: [], cost: { month: {} }, next: [], errors: [{ code: "gap", id: null, args: { a: "23:30", b: "00:30", min: 60, ia: "a1b2c3", ib: null }, text: gapText }] };
const me = list[1];
const withGap = sheetErrs(previewOf(gap).errors, 2, me);
const asOld = sheetErrs(previewOf(gap).errors, 1, list[0]);                    // правка первой: та же пара — её поле
const forged = sheetErrs(previewOf({ ok: true, schedule: [], cost: { month: {} }, next: [] }).errors, 2, me);
const fine = sheetErrs(previewOf({ ok: true, errors: [], cost: {}, next: [] }).errors, 2, me);
const refused = previewOf({ status: 400, code: "invalid", body: { ok: false, code: "invalid", errors: [{ code: "days", args: { n: 2 }, text: "Выберите хотя бы один день." }] } });
const perDay = sheetErrs([{ code: "per_day", id: null, args: { day: 1, n: 7, max: 6 }, text: "Не больше 6" },
  { code: "per_day", id: null, args: { day: 3, n: 7, max: 6 }, text: "другой день" }, { code: "time", args: { n: 1 }, text: "чужая" }], 2, me);
console.log(JSON.stringify({ body, withGap, asOld, forged, fine, refused, perDay }));
""")
        self.assertEqual(out["body"]["schedule"], [
            {"id": "a1b2c3", "time": "23:30", "mode": "quick", "days": [7], "on": True, "quiet": True, "monthly": False},
            {"time": "00:30", "mode": "quick", "days": [1], "on": True, "quiet": True, "monthly": False}])
        self.assertEqual(out["body"]["retry"], True)
        self.assertNotIn("tz", out["body"])                      # the preview takes no zone
        self.assertEqual(out["withGap"]["time"], ["Проверки в 23:30 и 00:30 — через 60 мин, нужно не меньше 2 ч."])
        self.assertEqual(len(out["asOld"]["time"]), 1)
        self.assertIs(out["withGap"]["canSave"], False)
        self.assertEqual([out["forged"][k] for k in ("time", "type", "days", "rep", "form")], [[], [], [], [], []])
        self.assertIs(out["forged"]["canSave"], True)
        self.assertIs(out["fine"]["canSave"], True)
        self.assertEqual([e["code"] for e in out["refused"]["errors"]], ["days"])
        self.assertEqual(out["perDay"]["days"], ["Не больше 6"])
        self.assertEqual(out["perDay"]["form"], ["другой день", "чужая"])   # another day, another entry — the common line

    def test_the_zone_goes_back_as_it_came(self):
        """REV-ns-b13: ``tz`` of PUT — exactly the one of GET, null too; ``confirm`` only when asked."""
        out = self.run_js(r"""
const l = [{ time: "04:00", mode: "full", days: [1], on: false, quiet: false, monthly: true }];
console.log(JSON.stringify({ named: bodyOf(l, { retry: false, tz: "Asia/Tokyo" }, { tz: true }),
  nul: bodyOf(l, { retry: true, tz: null }, { tz: true, confirm: true }), retry: bodyOf(l, { retry: true, tz: null }, { tz: true, retry: false }) }));
""")
        self.assertEqual(out["named"], {"schedule": [{"time": "04:00", "mode": "full", "days": [1], "on": False,
                                                      "quiet": False, "monthly": True}], "retry": False, "tz": "Asia/Tokyo"})
        self.assertIn("tz", out["nul"])
        self.assertIsNone(out["nul"]["tz"])
        self.assertIs(out["nul"]["confirm"], True)
        self.assertIs(out["retry"]["retry"], False)
        self.assertNotIn("confirm", out["retry"])

    def test_marks_and_the_filter_with_issues(self):
        """A check that found a sharp drop has the mark and is in «С проблемами»; the automatic repeat — «повтор»;
        skipped ones go with their switch; quick/full by the mode."""
        rows = [{"id": "1", "mode": "quick", "by": "schedule", "status": "ok", "drops": [{"link": "up", "value": 30}]},
                {"id": "2", "mode": "quick", "by": "auto", "retry_of": "1", "status": "ok", "drops": []},
                {"id": "3", "mode": "full", "by": "panel", "status": "ok", "drops": [], "under_load": True},
                {"id": "4", "mode": "quick", "by": "schedule", "status": "skipped", "drops": []},
                {"id": "5", "mode": "full", "by": "bot", "status": "ok", "drops": []},
                {"id": "6", "mode": "quick", "by": "cli", "status": "failed", "drops": []}]
        out = self.run_js(r"""
const rows = %s;
const ids = (l) => l.map((r) => r.id);
console.log(JSON.stringify({ flags: rows.map(runFlags), issues: ids(runFilter(rows, "issues", true)), noSkips: ids(runFilter(rows, "all", false)),
  quick: ids(runFilter(rows, "quick", true)), full: ids(runFilter(rows, "full", true)) }));
""" % json.dumps(rows))
        self.assertEqual(out["flags"], [["drop"], ["retry"], ["load"], ["skip"], [], ["failed"]])
        self.assertEqual(out["issues"], ["1", "2", "3", "4", "6"])
        self.assertEqual(out["noSkips"], ["1", "2", "3", "5", "6"])
        self.assertEqual(out["quick"], ["1", "2", "4", "6"])
        self.assertEqual(out["full"], ["3", "5"])

    def test_of_usual_only_from_the_server(self):
        """NS-D30: the number is the server's rel (rounded, also 0 and above 100 — no band of thresholds); none — «N из 3»
        only while usual_n is below the need (the n is clamped to 0…), else (a zero denominator) — nothing to say."""
        out = self.run_js(r"""
console.log(JSON.stringify({ rel: [relOf(96, 12, 3), relOf(0, 12, 3), relOf(287.6, 12, 3)], few: [relOf(null, 2, 3), relOf(null, -1, 3), relOf(undefined, "x", 3)],
  zero: relOf(null, 12, 3), nan: relOf(NaN, 12, 3), sign: [signOf(5), signOf(-0.5), signOf(0), signOf(null), signOf(Infinity)] }));
""")
        self.assertEqual(out["rel"], [{"k": "dg.rel", "v": {"n": 96}}, {"k": "dg.rel", "v": {"n": 0}}, {"k": "dg.rel", "v": {"n": 288}}])
        self.assertEqual(out["few"], [{"k": "dg.relNone", "v": {"n": 2, "need": 3}}, {"k": "dg.relNone", "v": {"n": 0, "need": 3}},
                                      {"k": "dg.relNone", "v": {"n": 0, "need": 3}}])
        self.assertIsNone(out["zero"])
        self.assertIsNone(out["nan"])
        self.assertEqual(out["sign"], ["+", "−", "", None, None])

    def test_days_of_an_entry(self):
        out = self.run_js(r"""
console.log(JSON.stringify([daysKind([1, 2, 3, 4, 5, 6, 7]), daysKind([5, 4, 3, 2, 1]), daysKind([7, 6]), daysKind([1, 4]), daysKind([])]));
""")
        self.assertEqual(out, ["daily", "weekdays", "weekends", "list", "list"])


if __name__ == "__main__":
    unittest.main()
