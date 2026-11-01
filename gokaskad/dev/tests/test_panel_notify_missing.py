"""REV-s1-rel П-2 (ветка выпуска 3.3.7): «Уведомлений» нет у сервера (GET /api/notify — 404: старое ядро под новой
панелью). Карточки нет, значит нет и записи поиска «Уведомления», и строки списка «Настроек» на телефоне — они вели бы
к пустому месту. 404 помнится и после ухода из «Настроек» (notifyReset), пока чтение не удастся.

Настоящие FIND_NEED, nf, notifyLoad, notifyReset, ST_ROWS и stRows из app.js (tests/jsparts.py) в node, без DOM.
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
    js = read("app.js")
    parts = [jsparts.const(js, n) for n in ("isObj", "FIND_NEED", "NOTIFY_KEY", "NOTIFY_KINDS", "nf", "nfKinds", "nfPick",
                                            "ST_ROWS", "ST_GROUPS", "stRow")]
    parts += [jsparts.function(js, n) for n in ("notifyLoad", "notifyReset", "stRows")]
    return r"""
const t = (k) => k, location = { host: "panel" };
const R = { gw: { data: null }, site: { data: null } };
const lookWord = () => "", gwEnabled = () => null, versionText = () => "3.3.7", goNav = () => {};
// UI-A1: «Время» есть у сервера, меток внимания нет (их проверяет tests/test_ui_a1_settings.py)
const tzf = { missing: false }, stAttData = () => ({}), stAttOf = () => null;
// 3.3.7.15 V2: подпись строки «Адреса» (часть pages/addresses.js ещё не загружена — пусто)
const adrRowS = () => "";
// blk: построить сейчас и заново на scheduleRefresh (как панель на свежих данных)
const BLKS = [];
function blk(compute, build) { const h = { kids: null }; const draw = () => { h.kids = build(compute()); }; draw(); BLKS.push(draw); return h; }
let REFRESH = 0;
function scheduleRefresh() { REFRESH++; BLKS.forEach((f) => f()); }
const window = { Kit: { mlist: (rows) => rows, mrow: (o) => o.key, mst: () => null, msec: (title, body) => body } };
// UI-5: список частей — группами (Kit.msec); узел — плоский список ключей строк
const el = (tag, attrs, ...kids) => kids.flat(Infinity).filter(Boolean);
// ответ GET /api/notify: ANSWER — объект (200) или число (код ошибки)
let ANSWER = 404;
const API = { notify: { get: () => (typeof ANSWER === "number" ? Promise.reject({ status: ANSWER, message: "HTTP " + ANSWER })
  : Promise.resolve(JSON.parse(JSON.stringify(ANSWER)))) } };
""" + "\n".join(parts)


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


@unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
class NotifyMissingTest(unittest.TestCase):
    def test_no_record_and_no_row_without_the_server_part(self):
        out = run(r"""
const rows = stRows();
const o = { find0: FIND_NEED.notify(), row0: rows.kids.indexOf("st:notify") >= 0 };
await notifyLoad();                                   // 404 — нет у сервера
o.find1 = FIND_NEED.notify(); o.row1 = rows.kids.indexOf("st:notify") >= 0; o.refreshed = REFRESH > 0;
o.others = rows.kids.length;
notifyReset();                                        // ушли из «Настроек»: 404 помнится
o.find2 = FIND_NEED.notify(); o.row2 = stRows().kids.indexOf("st:notify") >= 0;
ANSWER = { push: { exit_dead: true }, flap_summary: true, ver: "v1" };   // ядро обновили — чтение удалось
await notifyLoad();
o.find3 = FIND_NEED.notify(); o.row3 = stRows().kids.indexOf("st:notify") >= 0;
console.log(JSON.stringify(o));
""")
        self.assertEqual((out["find0"], out["row0"]), (True, True))      # не знаем — показываем
        self.assertEqual((out["find1"], out["row1"], out["refreshed"]), (False, False, True))
        self.assertEqual(out["others"], 12)                                # остальные строки на месте (UI-A1: + Время, Хранение, Проверка; 3.3.7.15: + Адреса)
        self.assertEqual((out["find2"], out["row2"]), (False, False))
        self.assertEqual((out["find3"], out["row3"]), (True, True))

    def test_other_error_keeps_the_record(self):
        """Не 404 (сеть, 500) — карточка покажет ошибку и «Повторить»: запись и строка остаются."""
        out = run(r"""
ANSWER = 500;
await notifyLoad();
console.log(JSON.stringify({ find: FIND_NEED.notify(), row: stRows().kids.indexOf("st:notify") >= 0, err: nf.err }));
""")
        self.assertEqual(out, {"find": True, "row": True, "err": "HTTP 500"})

    def test_the_catalog_record_names_the_condition(self):
        js = read("app.js")
        line = [x for x in js.splitlines() if '{ id: "settings.notify", kind: "card"' in x]
        self.assertEqual(len(line), 1)
        self.assertRegex(line[0], r'need: "notify"')
        self.assertTrue(re.search(r"^    notify: \(\) => !nf\.missing,$", js, re.M))


if __name__ == "__main__":
    unittest.main()
