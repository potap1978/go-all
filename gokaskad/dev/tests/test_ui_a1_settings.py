"""UI-A1 шаг 2b (эталон 23.10): «Настройки» — логика новых частей без DOM, настоящие куски app.js в node (tests/jsparts.py).

* «Время»: тело PUT /api/settings/time — пояс и ver; 404 — части нет (FIND_NEED.time, stHas); 409 — перечитать: чужая
  смена пояса, пока правили, — список «Применить снова», другой ver при том же поясе — молча; 500 — ошибка в части.
  То же на живом сервере — tests/test_panel_live_s1.py (test_the_time_card_on_the_live_server).
* Метки внимания подменю: приоритет «правки» > «выключен» > «истекает» > «не настроен»; источник — уже загруженное.
* «Уведомления» (Н8): каждый вид сервера — ровно в одной группе (вид без группы — в «Прочее»), «Проверка сервера» — своей
  группой; «Включено N из M» и «Все» — события без «Как присылать»; слово о боте.
* «Проверка сервера»: частота, вид, следующая, цена, порог ▲/▼ из ответов API; без балла.
Node-less: skipped."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from tests import jsparts

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def scope():
    js, kit = read("app.js"), read("kit.js")
    parts = [jsparts.const(js, n) for n in ("DASH", "known", "num", "arr", "isObj", "siteLe", "NOTIFY_KEY", "NOTIFY_KINDS", "nf", "nfKinds",
                                            "tzf", "tmCount", "tmBody", "tmNow", "ST_CERT_DAYS", "NT_GROUPS", "NT_SOON", "nfOnOf", "hc", "HC_PCT", "ST_PARTS", "stHas", "FIND_NEED")]
    parts += [jsparts.function(js, n) for n in ("tmTake", "timeLoad", "timeReset", "tmOffset", "tmOffText", "stBotSeen", "stAttData",
                                                "stAttOf", "nfGroupsOf", "nfBotChip", "healthLoad", "healthReset",
                                                "hcSavePct", "hcPctField")]
    parts += [jsparts.const(kit, n) for n in ("getAt", "same", "copy", "membersOf")]
    parts += [jsparts.function(kit, n) for n in ("setAt", "dirtyKeys", "reorder", "rebaseModel")]
    return r"""
const LOCALE = "ru-RU";
// словарь: ключ и подстановки — «ключ{a=1}»; формы «|» — по числу (tn)
const t = (k, v) => k + (v ? JSON.stringify(v) : "");
const tn = (k, n) => k + "#" + n;
const fmtInt = (n) => String(n), fmtNum1 = (n) => String(n);
const window = {};
let REFRESH = 0;
const scheduleRefresh = () => { REFRESH++; };
const setTimeout = (f) => 0;
const stAttSync = () => {};
let DIRTY = false, stCur = "", GW = null;
const pageDirty = () => DIRTY, gwEnabled = () => GW;
const R = { site: { loaded: true, missing: false, data: null }, panel: { loaded: true, missing: false, data: null }, rules: {}, awg: {}, gw: {}, forwards: {} };
const rd = () => R.site.data;
let stBot = null;
// ответы API: число — код ошибки, объект — 200
let ANSWER = null, PUT_BODY = null;
const ans = (a) => (typeof a === "number" ? Promise.reject({ status: a, message: "HTTP " + a }) : Promise.resolve(JSON.parse(JSON.stringify(a))));
const API = { time: { get: () => ans(ANSWER), put: (b) => { PUT_BODY = b; return ans(ANSWER); } } };
""" + "\n".join(parts) + "\nwindow.Kit = { dirtyKeys, rebaseModel };\n"


def run(script):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(scope() + "\n(async () => {\n%s\n})().catch((e) => { console.log(JSON.stringify({crash: String(e && "
                "e.stack || e)})); });\n" % script)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    out = res.stdout.decode("utf-8").strip().splitlines()
    if res.returncode != 0 or not out:
        raise AssertionError("node: %s" % res.stderr.decode("utf-8", "replace")[-2000:])
    doc = json.loads(out[-1])
    if "crash" in doc:
        raise AssertionError(doc["crash"])
    return doc


TIME = {"ok": True, "tz": "Europe/Moscow", "os_tz": "Etc/UTC", "now": 1761200000, "offset": 10800, "ntp": True,
        "zones": ["Asia/Tbilisi", "Etc/UTC", "Europe/Berlin", "Europe/Moscow"], "ver": "v1"}


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class TimeFormTest(unittest.TestCase):
    def test_body_and_404(self):
        out = run(r"""
ANSWER = %s;
await timeLoad();
const o = { base: tzf.base, count0: tmCount(), has: stHas("time"), find: FIND_NEED.time() };
tzf.draft.tz = "Europe/Berlin";
o.count1 = tmCount(); o.body = tmBody();
timeReset();
ANSWER = 404;
await timeLoad();
o.missing = [tzf.missing, stHas("time"), FIND_NEED.time(), REFRESH > 0];
timeReset();                                         // ушли из «Настроек»: 404 помнится до удачного чтения
o.missing2 = stHas("time");
ANSWER = 500;
await timeLoad();
o.err = [tzf.err, tzf.missing];
console.log(JSON.stringify(o));
""" % json.dumps(TIME))
        self.assertEqual(out["base"], {"tz": "Europe/Moscow"})
        self.assertEqual((out["count0"], out["has"], out["find"]), (0, True, True))
        self.assertEqual(out["count1"], 1)
        self.assertEqual(out["body"], {"ver": "v1", "tz": "Europe/Berlin"})   # ver обязателен: без него сервер — 400
        self.assertEqual(out["missing"], [True, False, False, True])
        self.assertFalse(out["missing2"])
        self.assertEqual(out["err"], ["HTTP 500", True])      # 500 — ошибка в части, а 404 помнится до удачного чтения

    def test_conflict_rebase(self):
        """409: перечитать с keep — пояс сменили в другом месте (другой, чем мой) — список «Применить снова»; ver другой,
        а пояс тот же — моя правка остаётся молча; мой пояс уже такой же — правки нет."""
        out = run(r"""
const A = %s;
const go = async (fresh, mine) => {
  timeReset(); ANSWER = A; await timeLoad(); tzf.draft.tz = mine;
  ANSWER = Object.assign({}, A, fresh); const r = await timeLoad(true);
  return { mode: r.mode, draft: tzf.draft.tz, base: tzf.base.tz, ver: tzf.ver, list: tzf.list.map((e) => [e.key, e.mine, e.theirs]), body: tmBody() };
};
console.log(JSON.stringify({ silent: await go({ ver: "v2" }, "Europe/Berlin"), list: await go({ tz: "Asia/Tbilisi", ver: "v3" }, "Europe/Berlin"),
  same: await go({ tz: "Europe/Berlin", ver: "v4" }, "Europe/Berlin") }));
""" % json.dumps(TIME))
        self.assertEqual(out["silent"], {"mode": "silent", "draft": "Europe/Berlin", "base": "Europe/Moscow", "ver": "v2", "list": [],
                                         "body": {"ver": "v2", "tz": "Europe/Berlin"}})
        self.assertEqual(out["list"]["mode"], "list")
        self.assertEqual((out["list"]["draft"], out["list"]["base"]), ("Asia/Tbilisi", "Asia/Tbilisi"))
        self.assertEqual(out["list"]["list"], [["tz", "Europe/Berlin", "Asia/Tbilisi"]])
        self.assertEqual((out["same"]["mode"], out["same"]["list"]), ("list", []))
        self.assertEqual(out["same"]["draft"], "Europe/Berlin")

    def test_offset_text(self):
        out = run(r"""
ANSWER = %s; await timeLoad();
console.log(JSON.stringify({ srv: tmOffset("Europe/Moscow", 1761200000), utc: tmOffset("Etc/UTC", 1761200000), t: [tmOffText(10800), tmOffText(19800), tmOffText(-10800), tmOffText(0), tmOffText(null)] }));
""" % json.dumps(dict(TIME, offset=14400)))
        self.assertEqual(out["srv"], 14400)                  # у сохранённого пояса — сдвиг сервера, не браузера
        self.assertEqual(out["utc"], 0)
        self.assertEqual(out["t"], ["UTC+3", "UTC+5:30", "UTC−3", "UTC", ""])


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class MarksTest(unittest.TestCase):
    def test_priority_and_sources(self):
        out = run(r"""
const le = (days, staging) => ({ domain: "vpn.example.com", cert: { kind: "letsencrypt", days, staging: !!staging } });
const at = (id) => { const a = stAttOf(id); return a ? a.cls + "|" + a.word : null; };
const o = {};
o.none = [at("site"), at("bot"), at("backup"), at("gateway")];
R.site.data = le(12); o.cert12 = at("site");
R.site.data = le(21); o.cert21 = at("site");
R.site.data = le(-1); o.certGone = at("site");
R.site.data = le(3, true); o.staging = at("site");          // тестовый сертификат — своё предупреждение в карточке
R.site.data = le(5); R.site.missing = true; o.siteMissing = at("site"); R.site.missing = false;
stBotSeen({ token: false, chat: false }); o.botNone = at("bot");
stBotSeen({ token: true, chat: false, running: false }); o.botNoChat = [at("bot"), stAttOf("bot").full];
stBotSeen({ token: true, chat: true, running: true }); o.botOk = at("bot");
stBotSeen({ token: false }); stCur = "bot"; DIRTY = true; o.dirtyFirst = at("bot");
stCur = "site"; o.dirtySite = at("site"); DIRTY = false;
GW = false; o.gwOff = at("gateway");
console.log(JSON.stringify(o));
""")
        self.assertEqual(out["none"], [None, None, None, None])     # данных нет — меток нет; копии — нет даты в API
        self.assertEqual(out["cert12"], "gx-att--warn|st.attCert")
        self.assertIsNone(out["cert21"])                            # тот же срок, что предупреждение карточки (< 21)
        self.assertEqual(out["certGone"], "gx-att--warn|st.attCertGone")
        self.assertIsNone(out["staging"])
        self.assertIsNone(out["siteMissing"])
        self.assertEqual(out["botNone"], "gx-att--none|st.attBot")
        self.assertEqual(out["botNoChat"], ["gx-att--none|st.attBot", "st.attBotChatT"])
        self.assertIsNone(out["botOk"])
        self.assertEqual((out["dirtyFirst"], out["dirtySite"]), ("gx-att--dirty|st.attDirty", "gx-att--dirty|st.attDirty"))
        self.assertEqual(out["gwOff"], "gx-att--none|st.attGwOff")


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class NotifyGroupsTest(unittest.TestCase):
    def test_every_kind_once(self):
        out = run(r"""
const kinds = NOTIFY_KINDS.concat(["brand_new_kind"]);
const g = nfGroupsOf(kinds, true);
const flat = [].concat(...g.filter((x) => !x.how).map((x) => x.keys));
const draft = { push: Object.fromEntries(kinds.map((k, i) => [k, i % 2 === 0])) };
draft.push.coalesce = true;
console.log(JSON.stringify({ ids: g.map((x) => x.id), flat, bench: g.find((x) => x.id === "bench").keys, other: g.find((x) => x.id === "other").keys,
  how: g.find((x) => x.id === "how").keys, noCo: nfGroupsOf(kinds, false).find((x) => x.id === "how").keys, few: nfGroupsOf(["exit_dead"], false).map((x) => x.id),
  on: nfOnOf(draft, kinds), soon: NT_SOON.map((x) => [x[0], x[3]]),
  chip: [nfBotChip(null), nfBotChip({ token: false }), nfBotChip({ token: true, chat: false }), nfBotChip({ token: true, chat: true, running: false }), nfBotChip({ token: true, chat: true, running: true })] }));
""")
        self.assertEqual(out["ids"], ["exits", "groups", "server", "host", "bench", "other", "how"])
        self.assertEqual(len(out["flat"]), len(set(out["flat"])), "вид — в двух группах")
        self.assertIn("brand_new_kind", out["flat"])                   # вид, которого панель не знает, не теряется
        self.assertEqual(out["bench"], ["bench_done", "bench_skipped_day"])
        self.assertEqual(out["other"], ["brand_new_kind"])
        self.assertEqual((out["how"], out["noCo"]), (["coalesce", "flap"], ["flap"]))
        self.assertEqual(out["few"], ["exits", "how"])                 # пустых групп нет
        self.assertEqual(out["on"], 14)                                # «Включено N из M» — без coalesce
        self.assertEqual(out["soon"], [["rules", "n2.stage2"], ["people", "n2.stage3"]])
        self.assertEqual(out["chip"], [None, ["nt.botNone", "default"], ["nt.botNoChat", "warning"], ["nt.botOff", "warning"], ["nt.botOk", "success"]])

    def test_every_server_kind_has_a_group(self):
        """Виды сервера (model.NOTIFY_PUSH) — все в своих группах, а не в «Прочем»."""
        from kaskad.gw import model
        out = run(r"""
const kinds = %s;
console.log(JSON.stringify(nfGroupsOf(kinds, true).map((x) => [x.id, x.keys])));
""" % json.dumps(sorted(k for k in model.NOTIFY_PUSH if k != "coalesce")))
        groups = dict(out)
        self.assertNotIn("other", groups, groups.get("other"))


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class HealthCardTest(unittest.TestCase):
    # круг 1 Т1: строк «Частота / Вид / Следующая / Трафик / Процессор» (hcModel) больше нет — сводка цены и следующая
    # проверка — у самого расписания (pages/diag.js sumNode, nextLine) в «Настройки › Проверка сервера»

    def test_no_command_in_the_texts(self):
        """Круг 1 №42 / П5: в текстах карточки «Проверка сервера» для человека нет команды kaskadctl (RU и EN)."""
        for lang in ("ru", "en"):
            words = dict(re.findall(r'"(hc\.[A-Za-z]+)": "([^"]*)"', read("i18n-%s.js" % lang)))
            self.assertTrue(words, lang)
            for key, txt in words.items():
                self.assertNotIn("kaskadctl", txt, (lang, key))
                self.assertNotIn("--dev-threshold", txt, (lang, key))

    def test_the_threshold_number_is_shown_once(self):
        """REV-3377-final П-2: число порога ▲/▼ — один раз, в выборе «Порог отметок ▲/▼» (карточка «Отметки ▲/▼»)."""
        out = run(r"""
globalThis.el = (tag, attrs, ...kids) => ({ tag, attrs, kids });
globalThis.U = { select: (opts, cur, on, o) => ({ opts, cur, o }), field: (label, ctl) => ({ label, ctl }) };
const f = hcPctField({ pct: 15 });
console.log(JSON.stringify({ f: JSON.stringify(f) }));
""")
        self.assertIn('"l":"15%"', out["f"])                               # число — в выборе
        self.assertNotIn("hc.pctHint{", out["f"])                          # подсказка — без числа

    def test_load(self):
        # круг 1 Т1: часть читает только порог ▲/▼ (расписание читает свой модуль); порог не пришёл — null, не undefined
        # (иначе часть перечитывает на каждой сборке)
        out = run(r"""
const res = {};
const go = async (sched, score) => {
  healthReset();
  API.host = { score: () => ans(score), bench: { schedule: () => ans(sched) } };
  await healthLoad();
  return { rule: hc.rule === undefined ? "undef" : hc.rule, busy: hc.busy };
};
res.ok = await go({ ok: true, schedule: [] }, { dev_rule: { pct: 15 } });
res.bad = await go(null, { charts: true });
res.fail = await go(503, 404);
console.log(JSON.stringify(res));
""")
        self.assertEqual(out["ok"], {"rule": {"pct": 15}, "busy": False})
        self.assertEqual(out["bad"], {"rule": None, "busy": False})
        self.assertEqual(out["fail"], {"rule": None, "busy": False})

    def test_the_threshold_is_saved(self):
        """UI-C: «Настройки › Проверка сервера» — порог ▲/▼ выбором 5…30 % (значение, заданное командой вне шага, — тоже
        в списке); выбор — POST /api/host/dev-rule {pct} (маршрут сервера: вход администратора, CSRF, строгая проверка),
        ответ сервера — новое правило и «Сохранено»; пока запрос идёт — второй не уходит, выбор выключен; ошибка —
        тост с текстом сервера, правило прежнее; порога нет в ответе — выбор выключен."""
        out = run(r"""
const SENT = [], TOASTS = [];
let REPLY = null;
globalThis.toast = (a, b, c) => { TOASTS.push([a, b, c]); };
globalThis.el = (tag, attrs, ...kids) => ({ tag, attrs, kids });
globalThis.U = { select: (opts, cur, on, o) => ({ opts, cur, on, o }), field: (label, ctl) => ({ label, ctl }) };
API.host = { devRule: (b) => { SENT.push(b); return REPLY(); } };
healthReset(); hc.rule = { pct: 15, use_spread: true };
const f = hcPctField(hc.rule), sel = f.kids[0].ctl;
const f12 = hcPctField({ pct: 12 }).kids[0].ctl, none = hcPctField(null).kids[0].ctl;
let done;
REPLY = () => new Promise((r) => { done = r; });
const p1 = sel.on("20");
const busy = hc.saving, second = hcSavePct("25"), busyField = hcPctField(hc.rule).kids[0].ctl.o.dis;
done({ ok: true, dev_rule: { pct: 20, use_spread: true }, changed: true }); await p1;
const after = { rule: hc.rule, saving: hc.saving };
REPLY = () => Promise.reject({ status: 400, message: "Порог — целое число от 5 до 30." });
await hcSavePct("25");
const bad = hcSavePct("abc");
console.log(JSON.stringify({ opts: sel.opts, cur: sel.cur, dis: sel.o.dis, aria: sel.o.aria, f12: f12.opts.map((x) => x.v), noneDis: none.o.dis, noneCur: none.cur,
  busy, second, busyField, after, sent: SENT, toasts: TOASTS, rule: hc.rule, bad, refresh: REFRESH }));
""")
        self.assertEqual([x["v"] for x in out["opts"]], ["5", "10", "15", "20", "25", "30"])
        self.assertEqual(out["opts"][0]["l"], "5%")
        self.assertEqual((out["cur"], out["dis"], out["aria"]), ("15", False, "hc.pct"))
        self.assertEqual(out["f12"], ["5", "10", "12", "15", "20", "25", "30"])
        self.assertEqual((out["noneDis"], out["noneCur"]), (True, None))
        self.assertTrue(out["busy"])
        self.assertIsNone(out["second"])                                  # второй запрос не уходит
        self.assertTrue(out["busyField"])
        self.assertEqual(out["after"], {"rule": {"pct": 20, "use_spread": True}, "saving": False})
        self.assertEqual(out["sent"], [{"pct": 20}, {"pct": 25}])         # строка выбора — числом
        self.assertEqual(out["toasts"], [['hc.pctSaved{"pct":"20"}', "", "success"],
                                         ["hc.pctFailed", "Порог — целое число от 5 до 30.", "danger"]])
        self.assertEqual(out["rule"], {"pct": 20, "use_spread": True})    # ошибка — правило прежнее
        self.assertIsNone(out["bad"])


if __name__ == "__main__":
    unittest.main()
