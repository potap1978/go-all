"""«Выбрать вручную» of the wizard, stage 4 (T-M6-qs330 №3: the step parsed the 14 MB catalog in the panel's process
and the OOM killer took the panel; the page stayed on «Загружаю…» forever).

    step   ``templates`` reads the index (prepare: the catalog and its index apart), pages of 25, the categories with
           their counts, «gone» from the health cache; errors are answers: no GitHub key — ``github_key`` with the
           action, a damaged catalog — its reason; never an exception, never a parse in the panel
    page   the REAL loadTemplates/tplList of setup.js (cut out by tests/jsparts.py) in node, fed with REAL answers of
           the step: loading state, «Показать ещё» appends the next page, a late answer of an old request is dropped,
           an error is a note with «Повторить» (no key — the card of the key), not an endless «Загружаю…»
"""

import json
import os
import shutil
import subprocess
import unittest
from unittest import mock

from kaskad import sysrun
from kaskad.errors import KaskadError
from kaskad.panel import auth
from kaskad.setup import steps
from kaskad.site import templates as T, tplhealth
from tests import jsparts
from tests.helpers import RootCase
from tests.setup.test_setup_core import new_link, open_panel
from tests.site import fixtures_templates as fx

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "lib", "kaskad",
                      "panel", "static", "setup")


def big_catalog(n=60):
    doc = fx.catalog_doc()
    doc["categories"][5]["templates"] += [fx._t("gh_r%02d" % i, "Ресторан %02d" % i, fx.GH + "r%d" % i)
                                          for i in range(n)]
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


class StepBase(RootCase):
    def setUp(self):
        RootCase.setUp(self)
        open_panel()
        new_link()
        self.runner = sysrun.FakeRunner()
        raw = big_catalog()
        for p in (mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online", lambda force=False: True),
                  mock.patch.object(T, "CATALOG_SHA256", T._sha(raw)), mock.patch.object(T, "BUILD_APART", False),
                  mock.patch.dict(os.environ, {"KASKAD_ASSETS_ROOTS": ""})):
            p.start()
            self.addCleanup(p.stop)
        path = T.catalog_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(raw)
        steps.run("password", {"password": "Correct-Horse-9"}, self.runner)


class TemplatesStepTest(StepBase):

    def test_pages_of_the_index_with_the_categories(self):
        out = steps.run("templates", {}, self.runner)
        self.assertEqual(out["state"], "ok")
        d = out["data"]
        self.assertEqual((d["page"], d["pages"], d["total"], len(d["items"])), (1, 4, 77, 25))
        self.assertEqual(d["categories"][0], {"id": "business", "title": "Бизнес и корпоративные", "count": 3})
        self.assertNotIn("legal", [c["id"] for c in d["categories"]])               # empty categories are no chips
        self.assertTrue(T.index_fresh())
        r = steps.run("templates", {"category": "restaurant", "page": 3}, self.runner)["data"]
        self.assertEqual((r["page"], r["pages"], r["total"], [i["id"] for i in r["items"]]),
                         (3, 3, 61, ["gh_r%02d" % i for i in range(49, 60)]))
        q = steps.run("templates", {"query": "ресторан"}, self.runner)["data"]
        self.assertEqual((q["total"], q["query"]), (61, "ресторан"))
        self.assertEqual(steps.run("templates", {"page": True}, self.runner)["data"]["page"], 1)

    def test_gone_is_marked_from_the_cache(self):
        tplhealth.mark("gh_good", "gone")
        items = steps.run("templates", {"category": "business"}, self.runner)["data"]["items"]
        self.assertEqual([(i["id"], i["gone"]) for i in items], [("gh_good", True), ("gh_dist", False),
                                                                  ("gh_cafe", False)])

    def test_unknown_category_is_an_answer(self):
        out = steps.run("templates", {"category": "nope"}, self.runner)
        self.assertEqual((out["ok"], out["error"]["code"]), (False, "unknown_category"))

    def test_no_key_is_the_card_of_the_key(self):
        os.unlink(T.catalog_path())
        with mock.patch.object(T.ghkey, "current", lambda: (None, None)):
            out = steps.run("templates", {}, self.runner)
        self.assertEqual((out["ok"], out["error"]["code"], out["error"]["action"]),
                         (False, "github_key", {"step": "github-key"}))
        self.assertIn("ключа GitHub", out["error"]["text"])

    def test_damaged_catalog_is_an_answer(self):
        with open(T.catalog_path(), "ab") as f:
            f.write(b" ")
        with mock.patch.object(T, "fetch_catalog", lambda runner=None: None):     # not brought again (no network)
            out = steps.run("templates", {}, self.runner)
        self.assertEqual((out["ok"], out["error"]["code"]), (False, "catalog_damaged"))

    def test_the_panel_builds_apart(self):
        """Under systemd (the panel's unit) the step starts the build as a separate process and parses nothing."""
        child = []

        def run(argv, input):
            child.append(argv)
            return 0, json.dumps(dict(T.build_index(), ok=True)), ""
        r = sysrun.FakeRunner(binaries=["systemd-run"]).on(["systemd-run"], run)
        real_build = T.build_index
        with mock.patch.dict(os.environ, {"INVOCATION_ID": "panel"}), mock.patch.object(T, "BUILD_APART", None), \
                mock.patch.object(T, "build_index", lambda: real_build() if child else self.fail("built in the panel")):
            out = steps.run("templates", {"category": "restaurant"}, r)
        self.assertEqual(out["data"]["total"], 61)
        self.assertEqual(child[0][:2], ["systemd-run", "--scope"])
        self.assertIn("--fetch", child[0])


NODE = r"""
const fs = require("fs");
const [setupPath, i18nPath, answersPath] = process.argv.slice(1);
global.window = {};
eval(fs.readFileSync(i18nPath, "utf8"));
const L = window.KASKAD_SETUP_I18N;
const PARTS = JSON.parse(fs.readFileSync(setupPath, "utf8"));
const ANS = JSON.parse(fs.readFileSync(answersPath, "utf8"));
const S = { busy: {}, res: {} }, UI = {};
const ui = (k) => (UI[k] = UI[k] || {});
const arr = (v) => (Array.isArray(v) ? v : []);
const t = (key, vars) => { let s = Object.prototype.hasOwnProperty.call(L, key) ? L[key] : "!" + key; if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] == null ? m : String(vars[k]))); return s; };
const h = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(9).filter((x) => x != null) });
const note = (kind, title, text, o) => ({ note: kind, title, text, btns: (o && o.btns) || [] });
const resNote = (res, o) => ({ note: "res", text: (res.error || {}).text || res.text, retry: o && o.retry });
const btn = (label, kind, fn, o) => ({ btn: label, fn, o: o || {} });
const ghCard = (gh, after) => ({ gh: true, after });
let renders = 0; const render = () => { renders++; };
const calls = [], pending = [];
const step = (name, body) => new Promise((resolve) => { calls.push(body); pending.push(resolve); });
eval(PARTS.join("\n"));
const texts = (nodes) => { const out = []; const walk = (n) => { if (n == null) return; if (typeof n === "string") out.push(n); else if (Array.isArray(n)) n.forEach(walk); else { if (n.attrs && n.attrs.text) out.push(n.attrs.text); if (n.btn) out.push("[" + n.btn + "]"); if (n.note) out.push("<" + n.note + ":" + (n.title || "") + "|" + (n.text || "") + ">"); if (n.gh) out.push("<gh>"); (n.kids || []).forEach(walk); } }; walk(nodes); return out; };
const tiles = (nodes) => { let n = 0; const walk = (x) => { if (x && typeof x === "object") { if (x.attrs && x.attrs.class === "qs-tpl") n++; (Array.isArray(x) ? x : (x.kids || [])).forEach(walk); } }; walk(nodes); return n; };
const tick = () => new Promise((r) => setImmediate(r));
(async () => {
  const out = {};
  const u = ui("domain");
  // 1. first load: the state says «loading», then page 1 of the server
  let p = loadTemplates();
  out.loading = texts(tplList(u));
  pending.shift()(ANS.page1); await p;
  out.first = { tiles: tiles(tplList(u)), texts: texts(tplList(u)), loading: u.tplLoading, body: calls[0] };
  // 2. «Показать ещё»: page 2 is appended
  const more = tplList(u).find((x) => x.kids && x.kids[0] && x.kids[0].btn && x.kids[0].o.k === "tpl-more").kids[0];
  p = loadTemplates(true);
  out.moreBusy = tplList(u).find((x) => x.kids && x.kids[0] && x.kids[0].o && x.kids[0].o.k === "tpl-more").kids[0].o.busy;
  pending.shift()(ANS.page2); await p;
  out.more = { label: more.btn, tiles: tiles(tplList(u)), body: calls[1], page: u.tpls.page };
  // 3. a category, then a word before the category answered: the late answer is dropped
  u.cat = "restaurant"; const pa = loadTemplates();
  u.q = "ресторан 5"; const pb = loadTemplates();
  pending.pop()(ANS.search); await pb;
  pending.pop()(ANS.category); await pa;
  out.race = { total: u.tpls.total, query: u.tpls.query, bodies: calls.slice(2) };
  // 4. an error: a note with «Повторить», no endless loading, the list stays
  p = loadTemplates(); pending.shift()(ANS.error); await p;
  out.error = { texts: texts(tplList(u)), loading: u.tplLoading, retry: typeof tplList(u)[0].retry };
  tplList(u)[0].retry(); pending.shift()(ANS.search); await tick();
  out.retried = { err: u.tplErr, total: u.tpls.total };
  // 5. no GitHub key: the card of the key; after the key — the list again
  u.tpls = null; u.cat = ""; u.q = ""; p = loadTemplates(); pending.shift()(ANS.nokey); await p;
  const l = tplList(u);
  out.nokey = { texts: texts(l), gh: l.some((x) => x.gh) };
  l.find((x) => x.gh).after(); pending.shift()(ANS.page1); await tick();
  out.afterKey = tiles(tplList(u));
  // 6. a failed «more» retries «more»
  p = loadTemplates(true); pending.shift()(ANS.error); await p;
  tplList(u)[0].retry(); out.moreRetry = calls[calls.length - 1];
  pending.shift()(ANS.page2); await tick();
  out.chips = tplNum(33467);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e.stack); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "нет node")
class TemplatesPageTest(StepBase):

    def answers(self):
        net = {"ok": False, "state": "error", "step": "templates", "error": {"code": "net", "text": "Нет связи"}}
        a = {"page1": steps.run("templates", {}, self.runner), "page2": steps.run("templates", {"page": 2}, self.runner),
             "category": steps.run("templates", {"category": "restaurant"}, self.runner),
             "search": steps.run("templates", {"category": "restaurant", "query": "ресторан 5"}, self.runner),
             "error": net}
        os.unlink(T.index_path())
        os.unlink(T.catalog_path())
        with mock.patch.object(T.ghkey, "current", lambda: (None, None)):
            a["nokey"] = steps.run("templates", {}, self.runner)
        return a

    def run_node(self, answers):
        with open(os.path.join(STATIC, "setup.js"), encoding="utf-8") as f:
            js = f.read()
        parts = [jsparts.const(js, "tplSeq", keyword="let"), jsparts.const(js, "tplNum"),
                 jsparts.function(js, "loadTemplates"), jsparts.function(js, "tplList"), "globalThis.tplNum = tplNum;"]
        d = self.root
        with open(os.path.join(d, "parts.json"), "w", encoding="utf-8") as f:
            json.dump(parts, f)
        with open(os.path.join(d, "answers.json"), "w", encoding="utf-8") as f:
            json.dump(answers, f, ensure_ascii=False)
        p = subprocess.run(["node", "-e", NODE, os.path.join(d, "parts.json"), os.path.join(STATIC, "setup-i18n-ru.js"),
                            os.path.join(d, "answers.json")], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_the_page_lists_pages_and_errors(self):
        a = self.answers()
        self.assertEqual(a["nokey"]["error"]["code"], "github_key")
        got = self.run_node(a)
        self.assertEqual(got["loading"], ["Открываю каталог шаблонов — в первый раз до минуты…"])
        self.assertEqual((got["first"]["tiles"], got["first"]["loading"], got["first"]["body"]), (25, "", {}))
        self.assertIn("Найдено 77 · показано 25", got["first"]["texts"])
        self.assertIn("[Показать ещё (52)]", got["first"]["texts"])
        self.assertTrue(got["moreBusy"])
        self.assertEqual(got["more"], {"label": "Показать ещё (52)", "tiles": 50, "body": {"page": 2}, "page": 2})
        self.assertEqual(got["race"], {"total": 10, "query": "ресторан 5", "bodies": [
            {"category": "restaurant"}, {"category": "restaurant", "query": "ресторан 5"}]})
        self.assertEqual((got["error"]["loading"], got["error"]["retry"]), ("", "function"))
        self.assertIn("<res:|Нет связи>", got["error"]["texts"])
        self.assertNotIn("Открываю каталог шаблонов — в первый раз до минуты…", got["error"]["texts"])
        self.assertEqual(got["retried"], {"err": None, "total": 10})
        self.assertTrue(got["nokey"]["gh"])
        self.assertTrue(any(x.startswith("<warning:Каталог шаблонов не открылся|") for x in got["nokey"]["texts"]))
        self.assertEqual(got["afterKey"], 25)
        self.assertEqual(got["moreRetry"], {"page": 2})
        self.assertEqual(got["chips"], "33 467")


if __name__ == "__main__":
    unittest.main()
