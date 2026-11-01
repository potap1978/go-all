"""Kaskad 3.3.0, the live check of the wizard «Быстрый старт» (k3/stand/logs/T-M6-qs330.md §1), the findings of e:

    №5   «Через выходы» after «Напрямую» brings the route back to the pool (or with the first key added there);
         «Позже» says what the route is;
    №6   one server once: VLESS by uuid@host:port (the menu's parser and the core say the same), the preview says
         «уже в пуле» for a key of the same server written otherwise;
    №7   the window of a key: the job of the wizard told in words, never by the inner name of a step; the window keeps
         the key and shows the answer in itself;
    №8   «Дополнительно»: the state of the subscriptions by «exit-sub list», the same subscription again is not an
         error, a subscription whose server does not exist is refused before «добавить?»;
    №10  the refusal of an inner address without «X → X»;
    №11  the ports: a port the install draws itself is «выберем сами»; «Соберём модуль» only when it is to be built;
    №12  the tour ends with «Готово»; «Сводка» says «на связи» by the collector;
    №13  the command of the AI agent: one address before and after the key;
    №15  «Неверный адрес» at the button; the tick of the rail next to a name in two lines;
    POLISH 4–5 of the panel: no stretched empty cards of «Протоколы», the name «OpenConnect» never torn.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from kaskad import state, sysrun
from kaskad.errors import ConflictError, KaskadError
from kaskad.gw import exits, exitsubs, ops, subfetch
from kaskad.panel import auth
from kaskad.setup import jobs, model, steps
from tests import jsparts
from tests.fakes_gw import VLESS_A, VLESS_B, write_pool
from tests.helpers import RootCase
from tests.setup.test_setup_core import new_link, open_panel
from tests.test_gw_exitsubs import IP_URL, SubsCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class WizardCase(RootCase):
    """The steps of the wizard on a fake runner, the password set (as tests/setup/test_setup_core.StepsTest)."""

    def setUp(self):
        RootCase.setUp(self)
        open_panel()
        new_link()
        self.runner = sysrun.FakeRunner()
        for p in (mock.patch.object(auth, "SCRYPT_N", 1 << 10), mock.patch.object(steps, "online", lambda force=False: True)):
            p.start()
            self.addCleanup(p.stop)
        steps.run("password", {"password": "Correct-Horse-9"}, self.runner)
        model.update(lambda d: d.update(current="exits"))
        self.route = {"via": "pool", "fail": "block"}
        self.routed = []
        self.exits = []

        def default_route(runner, via, fail=None):
            self.routed.append(via)
            self.route = {"via": via, "fail": fail or self.route["fail"]}
            return {"ok": True, "route": dict(self.route)}
        for p in (mock.patch("kaskad.gw.ops.default_route", default_route),
                  mock.patch("kaskad.gw.ops.exits_view", lambda r: list(self.exits)),
                  mock.patch.object(steps, "_route_doc", lambda: dict(self.route))):
            p.start()
            self.addCleanup(p.stop)


# ── №5 «Через выходы» after «Напрямую» ─────────────────────────────────────

class ExitsChoiceTest(WizardCase):
    def test_exits_after_direct_brings_the_pool_back(self):
        self.exits = [{"id": "e-1", "usable": True}]
        steps.run("exits-choice", {"choice": "direct"}, self.runner)
        self.assertEqual(self.route["via"], "direct")
        out = steps.run("exits-choice", {"choice": "exits"}, self.runner)
        self.assertTrue(out["ok"])
        self.assertEqual(self.routed, ["direct", "pool"])
        self.assertEqual(out["data"]["route"]["via"], "pool")       # the page shows the route of the answer at once

    def test_exits_without_keys_turn_the_pool_on_with_the_first_key(self):
        steps.run("exits-choice", {"choice": "direct"}, self.runner)
        steps.run("exits-choice", {"choice": "exits"}, self.runner)
        self.assertEqual(self.routed, ["direct"])                   # an empty pool would cut the devices off
        self.assertTrue(model.load()["answers"]["route_pool_on_add"])
        with mock.patch("kaskad.gw.ops.exit_preview", lambda r, t: {"items": []}), \
                mock.patch("kaskad.gw.ops.exit_add", lambda r, t: {"ok": True, "text": "Добавлен.", "results": []}):
            out = steps.run("exits-add", {"text": VLESS_A}, self.runner)
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.routed, ["direct", "pool"])
        self.assertFalse(model.load()["answers"]["route_pool_on_add"])

    def test_a_route_chosen_by_hand_is_not_changed_by_a_key(self):
        steps.run("exits-choice", {"choice": "direct"}, self.runner)
        steps.run("exits-choice", {"choice": "exits"}, self.runner)
        steps.run("route", {"via": "direct"}, self.runner)
        with mock.patch("kaskad.gw.ops.exit_preview", lambda r, t: {"items": []}), \
                mock.patch("kaskad.gw.ops.exit_add", lambda r, t: {"ok": True, "text": "", "results": []}):
            steps.run("exits-add", {"text": VLESS_A}, self.runner)
        self.assertEqual(self.routed, ["direct", "direct"])

    def test_later_says_what_the_route_is(self):
        self.exits = [{"id": "e-1", "usable": True}]
        out = steps.run("exits-choice", {"choice": "later"}, self.runner)
        self.assertEqual(self.routed, [])
        self.assertIn("через них", out["text"])
        model.update(lambda d: d.update(current="exits"))
        self.route = {"via": "direct", "fail": "block"}             # «Напрямую» before: the route is direct already
        out = steps.run("exits-choice", {"choice": "later"}, self.runner)
        self.assertIn("напрямую", out["text"])
        self.assertNotIn("не меняли", out["text"])

    def test_the_page_turns_the_pool_on_and_reads_the_list_after(self):
        js = read("setup/setup.js")
        f = jsparts.function(js, "chooseExits")
        self.assertLess(f.index('await step("exits-choice"'), f.index("startExitsPoll()"))
        # 3.3.3 (UX-333 §1): «Позже» нет — прежний ответ «later» (и «direct») страница решает по данным: выходы есть и
        # маршрут через них — «Через выходы», иначе «Сейчас без выхода» (R-333 С2; сервер «later» ещё принимает: CLI)
        self.assertIn('if (a === "direct" || a === "later") { u.choice = ""; u.fromData = true; } else u.choice = "exits";', js)
        self.assertNotIn('opt("later"', js)


# ── №6 one server once ─────────────────────────────────────────────────────

VLESS_A2 = VLESS_A.replace("fp=chrome", "fp=firefox").replace("#Vienna", "#Wien%20(копия)")


class VlessSamePeerTest(SubsCase):
    def test_one_account_on_one_server_is_one_key(self):
        self.assertNotEqual(exits.exit_id(VLESS_A), exits.exit_id(VLESS_A2))
        self.assertEqual(exits.peer_key(VLESS_A), exits.peer_key(VLESS_A2))
        self.assertNotEqual(exits.peer_key(VLESS_A), exits.peer_key(VLESS_B))
        other_port = VLESS_A.replace("198.51.100.20:443", "198.51.100.20:8443")
        self.assertNotEqual(exits.peer_key(VLESS_A), exits.peer_key(other_port))
        no_port = VLESS_A.replace("198.51.100.20:443", "198.51.100.20")
        self.assertEqual(exits.peer_key(VLESS_A), exits.peer_key(no_port))      # 443 when not given
        upper = VLESS_A.replace("11111111-2222", "11111111-2222".upper())
        self.assertEqual(exits.peer_key(VLESS_A), exits.peer_key(upper))

    def test_the_parser_of_the_menu_says_the_same(self):
        """install.sh adds by «egress_parse.py peers» — one key of a server, as the core."""
        parser = exits.load_parser()
        self.assertGreaterEqual(parser.PARSER_VERSION, 11)
        self.assertEqual(parser.peer_key(VLESS_A), parser.peer_key(VLESS_A2))
        self.assertEqual(parser.peer_key(VLESS_A), exits.peer_key(VLESS_A))
        self.assertNotEqual(parser.peer_key(VLESS_A), parser.peer_key(VLESS_B))

    def test_preview_says_already_in_the_pool(self):
        write_pool([VLESS_A])
        res = ops.exit_preview(self.w.runner, VLESS_A2)
        eid = exits.exit_id(VLESS_A)
        self.assertEqual([(x["in_pool"], x.get("same_peer")) for x in res["items"]], [(True, eid)])
        self.assertEqual(res["text"], "Все ключи уже есть в пуле.")
        self.w.script.add_result = (4, "Новых ключей не добавлено (дубликаты или пусто).\n", "")
        with self.assertRaises(ConflictError) as c:
            ops.exit_add(self.w.runner, VLESS_A2)
        self.assertIn("этот ключ уже есть как %s («Vienna»), записан иначе" % eid, str(c.exception))

    def test_a_subscription_does_not_add_the_same_account_again(self):
        write_pool([VLESS_A])
        from tests.test_gw_exitsubs import b64
        res = self.add(IP_URL, b64([VLESS_A2, VLESS_B]))
        self.assertEqual(res["result"]["added"], 1)
        self.assertEqual(self.pool(), [VLESS_A, VLESS_B])

    def test_the_window_offers_nothing_to_add(self):
        # 3.3.3: the field of ExitsInput (ux.js, one with the panel) says it and closes «Добавить» —
        # tests/test_333d_ux.py DomTest.test_exits_input_by_domain_and_nothing_to_add
        ux = read("ux.js")
        self.assertIn("x.in_pool || x.problem", ux)
        self.assertIn("|| !!S.dupe || (!!S.err && !!S.act);", ux)      # Д1 r2: и пока ошибка с кнопкой (Ш5)


# ── №7 the window of a key and the words of a busy wizard ──────────────────

class BusyWordsTest(RootCase):
    def test_no_inner_name_of_a_step(self):
        with mock.patch.object(jobs, "LAUNCH", lambda j, s: None):
            jobs.start("exits-add", {})
            with self.assertRaises(ConflictError) as c:
                jobs.start("exits-add", {})
        text = str(c.exception)
        self.assertEqual(text, "Ещё проверяю предыдущий ключ — подождите, пока закончу.")
        self.assertNotIn("exits-add", text)
        self.assertNotIn("шаг", text)
        self.assertEqual(c.exception.data["step"], "exits-add")          # the page still knows which job it is

    def test_every_long_step_has_words(self):
        longs = sorted(n for n, s in steps.STEPS.items() if s.long)
        self.assertTrue(longs)
        for n in longs:
            self.assertIn(n, jobs.BUSY, n)
            self.assertNotRegex(jobs.busy_text(n), r"[a-z]-[a-z]|«[a-z-]+»")
        from kaskad.i18n import tr                       # 3.3.2: BUSY — ключи каталога, текст — на языке запроса
        self.assertEqual(jobs.busy_text("какой-то-новый"), tr(jobs.BUSY_OTHER))

    def test_the_mock_says_the_same(self):
        mock_js = read("setup/dev-mock-setup.js")
        from kaskad.i18n import tr
        for n, key in list(jobs.BUSY.items()) + [("other", jobs.BUSY_OTHER)]:
            self.assertIn(json.dumps(tr(key, "ru"), ensure_ascii=False), mock_js, n)
        self.assertNotIn("Сейчас идёт шаг", mock_js)


@unittest.skipUnless(shutil.which("node"), "нет node")
class AddKeyJsTest(unittest.TestCase):
    """3.3.3: a key or a subscription of ExitsInput goes through the long step «exits-add» — the Promise of the
    component is settled by the end of its own job (added: resolved with the answer; refused: rejected with the words
    and the data of the step — cert_domain, github_key…); the end of another job does not settle it."""

    def run_js(self, script):
        js = read("setup/setup.js")
        parts = [jsparts.function(js, n) for n in ("stepErr", "exitsAdd", "exitsAddDone")]
        src = """
const S = { job: null, busy: {}, res: {}, jobSeen: {} }, U = {};
const ui = (k) => (U[k] = U[k] || {});
const t = (k) => k;
let NEXT = null; const sent = [];
const step = async (name, body, o) => { sent.push([name, body, o]); return NEXT; };
%s
%s
""" % ("\n".join(parts), script)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
            path = f.name
        try:
            r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        finally:
            os.unlink(path)
        if r.returncode:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_the_promise_waits_for_its_own_job(self):
        doc = self.run_js("""
(async () => {
  const got = [];
  NEXT = { job: { id: "j1" } };
  const p = exitsAdd("vless://key").then((r) => got.push(["ok", r]), (e) => got.push(["no", e.message, e.code, e.data]));
  await new Promise((r) => setTimeout(r, 0));
  exitsAddDone({ id: "other" }, { ok: true, state: "ok", text: "чужое" });
  await new Promise((r) => setTimeout(r, 0));
  const early = got.length;
  exitsAddDone({ id: "j1" }, { ok: false, state: "error", data: { cert_domain: "vpn.example.org", same_server: true },
    error: { code: "cert_name", text: "Сертификат выдан на домен vpn.example.org" } });
  await p;
  NEXT = { ok: true, state: "ok", text: "Добавлено ключей: 1.", data: { added: [{ name: "Вена" }] } };
  await exitsAdd("vless://key2").then((r) => got.push(["ok", r]));
  console.log(JSON.stringify({ early, got, sent }));
})();
""")
        self.assertEqual(doc["early"], 0)
        self.assertEqual(doc["got"][0], ["no", "Сертификат выдан на домен vpn.example.org", "cert_name",
                                         {"cert_domain": "vpn.example.org", "same_server": True, "reason": "cert_name"}])
        self.assertEqual(doc["got"][1], ["ok", {"text": "Добавлено ключей: 1.", "added": [{"name": "Вена"}]}])
        self.assertEqual(doc["sent"][0], ["exits-add", {"text": "vless://key"}, {"quiet": True}])

    def test_wired(self):
        js = read("setup/setup.js")
        self.assertIn("exitsAddDone(job, job.result)", jsparts.function(js, "onJob"))
        ux = jsparts.function(js, "exitsUx")
        self.assertIn("X.exitsInput(", ux)
        self.assertIn("addKey: (b) => exitsAdd(b.key), addSub: (b) => exitsAdd(b.url)", ux)
        self.assertNotIn("openAddKey", js)



@unittest.skipUnless(shutil.which("node"), "нет node")
class AddKeyRaceTest(unittest.TestCase):
    """R-333i С1: the real step(), onJob(), jobDone(), exitsAdd() and exitsAddDone() of setup.js — the Promise of
    ExitsInput is settled whatever comes first: the answer 202, the job over SSE, or the quiet job of loadState; a late
    document «starting» of a job already seen does not overwrite its end."""

    def run_scen(self, scen):
        js = read("setup/setup.js")
        parts = [jsparts.function(js, n) for n in ("step", "onJob", "jobDone", "stepErr", "exitsAdd", "exitsAddDone")]
        src = r"""
const S = { job: null, busy: {}, res: {}, jobSeen: {} }, U = {};
const ui = (k) => (U[k] = U[k] || {});
const t = (k) => k; const render = () => {}; const pollJob = () => {}; const reloads = []; const reload = (n) => reloads.push(n);
const stateSoon = () => {}; const setOffline = () => {}; const switchDone = () => {}; const moveSoon = () => {};
const now = () => 0; let es = { readyState: 1 };
%s
%s
const SCEN = %s;
const RES = SCEN === "ok" ? { ok: true, state: "ok", text: "Добавлено ключей: 1.", data: { added: [{ name: "Вена" }] } }
  : { ok: false, state: "error", error: { code: "bad_input", text: "плохой ключ" } };
const DONE = { id: "j1", step: "exits-add", state: SCEN === "ok" ? "done" : "failed", result: RES };
async function api(path, body, o) {
  if (SCEN === "early" || SCEN === "ok") onJob(DONE);            // SSE: задание кончилось раньше ответа 202
  return { status: 202, body: { ok: true, job: { id: "j1", step: "exits-add", state: "starting", result: null } } };
}
(async () => {
  let got = "pending";
  exitsAdd("vless://x").then((r) => { got = "ok:" + r.text; }, (e) => { got = "no:" + e.message + ":" + e.code; });
  await new Promise((r) => setTimeout(r, 5));
  if (SCEN === "quiet") onJob(DONE, true);                       // loadState раньше события SSE
  if (SCEN === "normal") onJob(DONE);
  if (SCEN === "quiet" || SCEN === "normal") onJob(DONE);        // повтор того же итога ничего не меняет
  if (SCEN === "other") onJob({ id: "j0", step: "exits-add", state: "failed", result: RES });
  await new Promise((r) => setTimeout(r, 5));
  console.log(JSON.stringify({ got, wait: !!ui("exits").addWait, job: S.job && S.job.state, reloads }));
})();
""" % (jsparts.const(js, "jobRunning"), "\n".join(parts), json.dumps(scen))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
            path = f.name
        try:
            r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        finally:
            os.unlink(path)
        if r.returncode:
            raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
        return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])

    def test_the_end_came_before_the_answer(self):
        doc = self.run_scen("early")
        self.assertEqual(doc["got"], "no:плохой ключ:bad_input")
        self.assertFalse(doc["wait"])
        self.assertEqual(doc["job"], "failed", "документ 202 «starting» затёр пришедший итог")

    def test_added_before_the_answer(self):
        doc = self.run_scen("ok")
        self.assertEqual(doc["got"], "ok:Добавлено ключей: 1.")
        self.assertEqual(doc["job"], "done")

    def test_the_quiet_path_of_load_state(self):
        doc = self.run_scen("quiet")
        self.assertEqual(doc["got"], "no:плохой ключ:bad_input")
        self.assertFalse(doc["wait"])

    def test_the_end_after_the_answer(self):
        doc = self.run_scen("normal")
        self.assertEqual(doc["got"], "no:плохой ключ:bad_input")
        self.assertEqual(doc["reloads"], ["exits"])

    def test_another_job_does_not_settle_it(self):
        doc = self.run_scen("other")
        self.assertEqual(doc["got"], "pending")
        self.assertTrue(doc["wait"])

# ── №8 «Дополнительно»: subscriptions ──────────────────────────────────────

class SubsExtrasTest(WizardCase):
    def setUp(self):
        super().setUp()
        model.update(lambda d: d.update(current="extras"))

    def sub(self, auto=True):
        return {"id": "s-abc123", "n": 1, "url": "https://ga.example.org/s-x/all.vpn", "auto": auto, "keys": [],
                "seen": [], "excluded": [], "added": "", "last_ok": None, "last_try": None, "error": "", "count": 3,
                "full": 0, "pin": ""}

    def test_the_subscriptions_are_rows_of_the_exits(self):
        # 3.3.3 (UX-333, №37): no item «Автообновление подписки» in «Дополнительно» — the subscriptions are rows of
        # ExitsInput on the stage «Выходы» (name, keys, auto; never the address)
        exitsubs.save({"schema": 1, "next": 2, "subs": [self.sub()]})
        out = steps.run("extras-list", {}, self.runner)
        self.assertNotIn("sub", [x["id"] for x in out["data"]["items"]])
        with mock.patch("kaskad.gw.ops.exits_view", lambda r, st=None: []):
            out = steps.run("exits", {}, self.runner)
        self.assertEqual([(r["id"], r["name"], r["auto"]) for r in out["data"]["sub_rows"]], [("s-abc123", "ga.example.org", True)])
        self.assertNotIn("s-x", json.dumps(out))                              # the address (its path) never shown

    def test_a_subscription_is_deleted_with_its_keys(self):
        exitsubs.save({"schema": 1, "next": 2, "subs": [self.sub()]})
        bad = steps.run("exit-sub-del", {"id": "../x"}, self.runner)
        self.assertEqual(bad["error"]["code"], "bad_input")
        with mock.patch("kaskad.gw.exitsubs.timer_sync", lambda r, d: None):
            out = steps.run("exit-sub-del", {"id": "s-abc123"}, self.runner)
        self.assertTrue(out["ok"], out)
        self.assertEqual(exitsubs.load()["subs"], [])

    def test_keep_keys_as_the_panel_asks(self):
        # R-333i З1: the question of the panel — «Оставить ключи в пуле» sends keep: true (--keep-keys); anything but
        # true is «with its keys»
        exitsubs.save({"schema": 1, "next": 2, "subs": [self.sub()]})
        got = []
        fake = lambda r, ref, keep_keys=False: got.append((ref, keep_keys)) or {"ok": True, "removed": 0, "text": "ok"}
        with mock.patch("kaskad.gw.exitsubs.delete", fake):
            steps.run("exit-sub-del", {"id": "s-abc123", "keep": True}, self.runner)
            steps.run("exit-sub-del", {"id": "s-abc123", "keep": "yes"}, self.runner)
            steps.run("exit-sub-del", {"id": "s-abc123"}, self.runner)
        self.assertEqual(got, [("s-abc123", True), ("s-abc123", False), ("s-abc123", False)])

    def test_the_rows_say_stale_as_the_panel(self):
        # R-333i П2: «stale»/«error» as the panel shows them, the ids of its keys for the question; no address
        sub = dict(self.sub(), error="timeout", last_try=1, keys=["e-1"])
        exitsubs.save({"schema": 1, "next": 2, "subs": [sub]})
        with mock.patch("kaskad.gw.ops.exits_view", lambda r, st=None: []), \
                mock.patch("kaskad.gw.exitsubs.stale", lambda s, now=None: True):
            out = steps.run("exits", {}, self.runner)
        row = out["data"]["sub_rows"][0]
        self.assertEqual((row["stale"], row["error"]), (True, True))
        self.assertIn("exits", row)
        self.assertNotIn("s-x", json.dumps(out))
        self.assertIs(row["error"], True)                                     # a mark; the words are in «text»

    def test_the_same_subscription_again_is_not_an_error(self):
        exitsubs.save({"schema": 1, "next": 2, "subs": [self.sub()]})
        err = KaskadError("Эта подписка уже добавлена (ga.example.org).", code=3,
                          data={"ok": False, "error": "…", "code": 3, "data": {"sub": "s-abc123"}})
        with mock.patch("kaskad.setup.features.call", side_effect=err):
            out = steps.run("exits-add", {"text": "https://ga.example.org/s-x/all.vpn"}, self.runner)
        self.assertEqual((out["ok"], out["state"]), (True, "ok"))
        self.assertTrue(out["data"]["already"])
        self.assertIn("уже добавлена (ga.example.org)", out["text"])
        other = KaskadError("Подписок уже 20 — больше не добавить.", code=3, data={"ok": False, "code": 3})
        with mock.patch("kaskad.setup.features.call", side_effect=other):
            out = steps.run("exits-add", {"text": "https://ga.example.org/s-y/all.vpn"}, self.runner)
        self.assertEqual(out["state"], "error")

    def test_a_server_that_does_not_exist_is_refused_before_add(self):
        def resolve(host):
            if host == "nope.example.invalid":
                raise OSError("Name or service not known")
            return {"metadata.cloud": ["169.254.169.254"]}.get(host, ["1.1.1.1"])
        with mock.patch.object(subfetch, "_resolve", resolve), mock.patch.object(subfetch, "check_cert", lambda u: None), \
                mock.patch("kaskad.gw.ops.exit_preview", lambda r, t: {"subscription": True, "text": "", "items": []}):
            bad = steps.run("exits-preview", {"text": "https://nope.example.invalid/sub"}, self.runner)
            inner = steps.run("exits-preview", {"text": "http://metadata.cloud/latest"}, self.runner)
            good = steps.run("exits-preview", {"text": "https://ga.example.org/s-new/all.vpn"}, self.runner)
            exitsubs.save({"schema": 1, "next": 2, "subs": [self.sub()]})
            again = steps.run("exits-preview", {"text": "https://ga.example.org/s-x/all.vpn"}, self.runner)
        self.assertEqual((bad["ok"], bad["state"]), (False, "error"))
        self.assertIn("не найден (nope.example.invalid)", bad["text"])
        self.assertIn("внутренний адрес (metadata.cloud → 169.254.169.254)", inner["text"])
        self.assertTrue(good["ok"])
        self.assertEqual(good["text"], steps.SUB_FOUND_TEXT)
        self.assertNotIn("добавятся", good["text"])                           # no «done» before it is done
        self.assertTrue(good["data"]["subscription"] and good["data"]["sub_checked"])
        self.assertTrue(again["ok"] and again["data"]["sub_already"])
        self.assertIn("уже добавлена", again["text"])

    def test_a_slow_lookup_is_not_a_failure(self):
        def slow(host):
            time.sleep(0.5)
            return ["1.1.1.1"]
        with mock.patch.object(subfetch, "_resolve", slow), mock.patch.object(steps, "SUB_CHECK_TIMEOUT", 0.05), \
                mock.patch.object(subfetch, "check_cert", lambda u: None), \
                mock.patch("kaskad.gw.ops.exit_preview", lambda r, t: {"subscription": True, "text": "", "items": []}):
            out = steps.run("exits-preview", {"text": "https://slow.example.org/sub"}, self.runner)
        self.assertTrue(out["ok"])
        self.assertEqual(out["text"], steps.SUB_UNCHECKED_TEXT)
        self.assertFalse(out["data"]["sub_checked"])



# ── №10 the refusal of an inner address ────────────────────────────────────

class InternalTextTest(unittest.TestCase):
    def test_no_arrow_to_itself(self):
        with self.assertRaises(KaskadError) as c:
            subfetch.check_url("http://169.254.169.254/latest/meta-data")
        self.assertIn("(169.254.169.254)", str(c.exception))
        self.assertNotIn("→", str(c.exception))
        with self.assertRaises(KaskadError) as c:
            subfetch.check_url("http://[::1]:8080/x")
        self.assertNotIn("→", str(c.exception))
        with self.assertRaises(KaskadError) as c:
            subfetch.check_url("http://box.lan/x", resolve=lambda h: ["192.168.1.5"])
        self.assertIn("(box.lan → 192.168.1.5)", str(c.exception))


# ── №11 ports and the kernel module ────────────────────────────────────────

class ProtocolsPlanTest(SubsCase):
    def test_a_drawn_port_is_said_so(self):
        from kaskad.gw import registry
        for k in ("awg2", "wg"):                     # what the host can do is not the question here
            p = mock.patch.object(registry.get(k), "requirements", lambda runner: {"supported": True})
            p.start()
            self.addCleanup(p.stop)
        rows = dict((r["kind"], r) for r in ops.inbound_plan(self.w.runner, ["awg2", "wg"]))
        self.assertTrue(rows["awg2"]["port"]["random"])
        self.assertFalse(rows["wg"]["port"]["random"])

    def test_module_ready_and_the_words_of_the_page(self):
        js = read("setup/setup.js")
        doc = run_node("""
%s
%s
const P = (kind, o) => Object.assign({ kind, title: { awg2: "AmneziaWG 2.0", awg3: "AmneziaWG 3.1", wg: "WireGuard" }[kind], awg: /^awg/.test(kind), installed: false }, o || {});
console.log(JSON.stringify({
  drawn: portText(P("awg2", { port: { port: 44035, protos: ["udp"], conflicts: [], free: null, random: true } })),
  fixed: portText(P("wg", { port: { port: 51820, protos: ["udp"], conflicts: [], free: null, random: false } })),
  busy: portText(P("awg2", { port: { port: 44035, protos: ["udp"], conflicts: [{ detail: "x" }], free: 40001, random: true } })),
  installed: portText(P("awg2", { installed: true, port: { port: 59459, protos: ["udp"], conflicts: [], free: null, random: false } }))
}));
""" % ("const t = (k, v) => k + (v ? JSON.stringify(v) : \"\"); const arr = (x) => (Array.isArray(x) ? x : []);",
       jsparts.function(js, "portText")))
        self.assertEqual(doc["drawn"], 'pr.portAuto{"t":"AmneziaWG 2.0"}')
        self.assertEqual(doc["fixed"], "51820/udp")
        self.assertEqual(doc["busy"], "40001/udp")                 # the free port the wizard passes to the install
        self.assertEqual(doc["installed"], "59459/udp")
        # «Соберём модуль ядра» left the page (3.3.2 №16): the job says it while it builds
        self.assertNotIn("function moduleBuild", js)

    def test_the_plan_says_whether_the_module_is_there(self):
        open_panel()
        new_link()
        with mock.patch.object(auth, "SCRYPT_N", 1 << 10):
            steps.run("password", {"password": "Correct-Horse-9"}, self.w.runner)
        with mock.patch("kaskad.gw.awgkmod.module_loaded", lambda: True), \
                mock.patch("kaskad.gw.awgkmod.set_loaded", lambda: False), \
                mock.patch("kaskad.gw.ops.inbound_plan", lambda r, kinds: []), \
                mock.patch("kaskad.gw.awgengine.check", lambda r: {"can": True, "blockers": [], "codes": []}), \
                mock.patch.object(steps, "online", lambda force=False: False):
            out = steps.run("protocols-plan", {}, self.w.runner)
        mod = out["data"]["module"]
        self.assertEqual((mod["ready"], mod["ready31"], mod["program"]), (True, False, False))


def run_node(src):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if r.returncode:
        raise AssertionError(r.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(r.stdout.decode("utf-8").strip().splitlines()[-1])


ProtocolsPlanTest = unittest.skipUnless(shutil.which("node"), "нет node")(ProtocolsPlanTest)


# ── №12 the tour and the summary ───────────────────────────────────────────

class SummaryOnlineTest(WizardCase):
    def test_on_line_by_the_collector(self):
        from kaskad.gw import live, model as gmodel
        st = state.load()
        st.setdefault("gateway", {})
        users = [{"id": "u-1", "name": "Аня"}]
        devs = [{"id": "d-1", "name": "Аня · iPhone", "user": "u-1"}, {"id": "d-2", "name": "Аня · Mac", "user": "u-1"}]
        with mock.patch.object(gmodel, "users", lambda s: users), \
                mock.patch.object(gmodel, "devices", lambda s, user=None: [d for d in devs if d["user"] == user]), \
                mock.patch.object(live, "read", lambda: ({"devices": {"d-1": {"online": True}, "d-2": {"online": False}}}, 3)):
            out = steps.run("summary", {}, self.runner)
        self.assertEqual(out["data"]["people"][0]["online"], ["Аня · iPhone"])
        with mock.patch.object(gmodel, "users", lambda s: users), \
                mock.patch.object(gmodel, "devices", lambda s, user=None: [d for d in devs if d["user"] == user]), \
                mock.patch.object(live, "read", lambda: (None, None)):
            out = steps.run("summary", {}, self.runner)
        self.assertIsNone(out["data"]["people"][0]["online"])          # the collector silent: no fact, not «нет»


@unittest.skipUnless(shutil.which("node"), "нет node")
class TourAndChipJsTest(unittest.TestCase):
    def test_person_chip_by_the_fact_of_the_server(self):
        js = read("setup/setup.js")
        doc = run_node("""
const U = {}; const ui = (k) => (U[k] = U[k] || {}); const t = (k) => k; const arr = (x) => (Array.isArray(x) ? x : []);
const chip = (text, tone) => ({ text, tone });
%s
ui("done").devWho = "Аня";
console.log(JSON.stringify({
  reloaded: personChip({ name: "Аня", devices: ["Аня · iPhone"], online: ["Аня · iPhone"] }, undefined),
  waiting: personChip({ name: "Аня", devices: ["Аня · iPhone"], online: [] }, undefined),
  page: personChip({ name: "Аня", devices: ["Аня · iPhone"], online: null }, "traffic"),
  other: personChip({ name: "Боря", devices: ["Боря · Mac"], online: [] }, "traffic"),
  otherOn: personChip({ name: "Боря", devices: ["Боря · Mac"], online: ["Боря · Mac"] }, undefined)
}));
""" % jsparts.function(js, "personChip"))
        self.assertEqual(doc["reloaded"], {"text": "d.cOnline", "tone": "success"})
        self.assertEqual(doc["waiting"], {"text": "d.cWaiting", "tone": "warning"})
        self.assertEqual(doc["page"], {"text": "d.cOnline", "tone": "success"})
        self.assertIsNone(doc["other"])
        self.assertEqual(doc["otherOn"], {"text": "d.cOnline", "tone": "success"})

    def test_the_tour_left_the_wizard(self):
        # 3.3.2 №14 (the owner): the tour and the scheme are no longer in the wizard — the first acquaintance with the
        # panel shows them; the last tab of «Готово» ends with «Открыть панель» (finish)
        js = read("setup/setup.js")
        self.assertNotIn("function tourView", js)
        i = js.index("VIEW.done = () => {")
        view = js[i:jsparts.close_of(js, js.index("{", i)) + 1]
        self.assertNotIn('"tour"', view)
        self.assertNotIn('"scheme"', view)
        self.assertIn('label: t("d.openPanel")', js)


# ── №13 the address of the agent ───────────────────────────────────────────

class AgentHostTest(WizardCase):
    def test_one_address_before_and_after_the_key(self):
        from kaskad.mcp import keys as mkeys
        from kaskad.site import model as smodel
        with mock.patch("kaskad.gw.model.public_host", lambda s: "203.0.113.10"):
            self.assertEqual(mkeys.host_guess(), "203.0.113.10")
            st = state.load()
            st["site"] = {"domain": "vpn.example.ru", "mode": "domain", "cert": "letsencrypt"}
            state.save(st)
            with mock.patch.object(smodel, "active", lambda s: {"names": ["vpn.example.ru"]}):
                self.assertEqual(mkeys.host_guess(), "vpn.example.ru")
                # 3.3.3: the wizard shows AgentKey with the panel's GET /api/mcp — its «connect» is mkeys.connect()
                self.assertEqual(mkeys.connect()["host"], "vpn.example.ru")
            with mock.patch.object(smodel, "active", lambda s: None):        # no certificate — the IP
                self.assertEqual(mkeys.host_guess(), "203.0.113.10")

    def test_the_page_takes_the_address_of_the_server(self):
        js = read("setup/setup.js")
        self.assertIn('mcp: { get: () => uxCall("mcp")', js)                 # the panel's handler, its «connect»
        self.assertNotIn("mcpConnect", js)


# ── №15 the classic stage and the rail ─────────────────────────────────────

class ClassicAndRailTest(unittest.TestCase):
    def test_the_hint_says_the_address_is_wrong(self):
        js = read("setup/setup.js")
        view = js[js.index("VIEW.forward = () =>"):js.index("async function createForward")]
        self.assertIn('bad.to ? t("fw.badAddr")', view)
        i18n = read("setup/setup-i18n-ru.js")
        self.assertIn('"fw.badAddr": "Неверный адрес"', i18n)
        if shutil.which("node"):
            doc = run_node("%s\nconsole.log(JSON.stringify(['999.1.1.1', '192.0.2.4', '256.1.1.1', '1.2.3'].map(isIp)));"
                           % jsparts.const(js, "isIp"))
            self.assertEqual(doc, [False, True, False, False])

    def test_the_tick_is_next_to_the_name(self):
        css = read("setup/qs.css")
        rule = re.search(r"\.qs-step__t \{([^}]*)\}", css).group(1)
        self.assertIn("display: block", rule)
        self.assertNotIn("flex", rule)
        self.assertIn("white-space: nowrap", re.search(r"\.qs-step__last \{([^}]*)\}", css).group(1))
        self.assertIn("stepTitle(x.title", read("setup/setup.js"))


# ── POLISH 4–5: «Протоколы» of the panel ───────────────────────────────────

class ProtocolCardsTest(unittest.TestCase):
    def rule(self, css, sel):
        m = re.findall(r"(?m)^%s \{([^}]*)\}" % re.escape(sel), css)
        self.assertTrue(m, sel)
        return " ".join(m)

    def test_no_stretched_empty_cards(self):
        css = read("styles.css")
        self.assertIn("align-items: start", self.rule(css, ".gx-pgrid"))
        card = self.rule(css, ".gx-pcard")
        self.assertNotIn("subgrid", card)
        self.assertNotIn("span", card)
        self.assertNotRegex(css, r"\.gx-pcard(--short)? \{ grid-row: span")
        self.assertNotIn("align-self: end", self.rule(css, ".gx-pcard__acts"))

    def test_the_name_is_never_torn(self):
        css = read("styles.css")
        self.assertNotIn("anywhere", self.rule(css, ".gx-pcard__title"))
        self.assertIn("min-width: min-content", self.rule(css, ".gx-pcard__tt"))
        self.assertIn("flex-wrap: wrap", self.rule(css, ".gx-pcard__head"))
        card = jsparts.function(read("app.js"), "ibCard")
        self.assertIn('under = st.key === "na"', card)


if __name__ == "__main__":
    unittest.main()
