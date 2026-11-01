"""«Движок AmneziaWG» в панели (k3/SPEC-awg-engine.md §3–§4) на настоящей панели над поддельным узлом:

    P1  GET gw/awg/engine — файлы, ничего не запускается: совет pending после обновления, причина программы
        (контейнер), смешанный режим на модуле;
    P2  POST gw/awg/engine — смена через ядро (why «person:panel»), смешивание — 409 с кодом 3, --force проходит;
        откат — ok с rolled_back и причиной в предупреждениях;
    P3  POST gw/awg/export — секреты только в ответе no-store; import того же документа;
    P4  настоящие normAwg и awgOfferOf из app.js (node) на ответах сервера: offer / blocked / warn / none."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from kaskad import paths, secrets, state, util
from kaskad.errors import ConflictError
from kaskad.gw import awgengine, awgkmod, exits
from tests import jsparts
from tests.fakes_nft import F443, save_state
from tests.panel.panelcase import PanelCase

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib", "kaskad", "panel", "static")


def gw(kinds, engine=None):
    save_state([F443])
    with state.transaction() as st:
        st["gateway"] = {"enabled": False, "net": {"base": "10.77.0.0/16"},
                         "inbounds": dict((k, {"enabled": True, "port": 40000 + i, "params": {}})
                                          for i, k in enumerate(kinds)),
                         "users": [{"id": "u-00000001", "name": "Мама", "enabled": True}],
                         "devices": [{"id": "d-00000001", "user": "u-00000001", "name": "Телефон", "enabled": True,
                                      "kind": "phone", "platform": "android", "access": list(kinds), "ips": {}}],
                         "routes": {"default": {"via": "pool", "fail": "block"}}, "exits": {}}
        if engine:
            st["gateway"]["awg_engine"] = engine


def kmod(on):
    os.makedirs(paths.gw_dir(), exist_ok=True)
    util.atomic_write(awgkmod.state_path(), json.dumps({"enabled": on}), 0o600)
    if on:
        os.makedirs(awgkmod.module_dir(), exist_ok=True)


class AwgPanelTest(PanelCase):
    def test_p1_reads_run_nothing(self):
        gw(("awg2",), engine={"pending": True})
        before = len(self.w.runner.calls)
        doc = self.ok("GET", "/api/gw/awg/engine")
        self.assertEqual(len(self.w.runner.calls), before)
        self.assertEqual((doc["active"], doc["offer"]["show"], doc["offer"]["pending"]), ("program", True, True))
        self.assertIn("рекомендуется", doc["text"])
        gw(("awg2",), engine={"want": "kernel", "reason": "Здесь модуль ядра не загрузить: сервер — контейнер lxc.",
                              "code": "host"})
        doc = self.ok("GET", "/api/gw/awg/engine")
        self.assertFalse(doc["offer"]["show"])
        self.assertIn("контейнер lxc", doc["text"])

    def test_p2_switch_conflict_force_rollback(self):
        gw(("awg2", "awg3"))
        calls = []

        def switch(runner, engine, force=False, why="", **kw):
            calls.append((engine, force, why))
            if engine == "kernel" and not force:
                raise ConflictError("Смешивать программу и модуль нельзя: Вход AmneziaWG 3.1 …")
            if engine == "program":
                return {"ok": False, "engine": "kernel", "was": "kernel", "rolled_back": True, "reason": "клиенты не вернулись",
                        "warnings": []}
            return {"ok": True, "engine": engine, "was": "program", "backup": "/var/backups/kaskad/awg-engine-x",
                    "warnings": []}

        with mock.patch.object(awgengine, "switch", switch):
            r = self.call("POST", "/api/gw/awg/engine", {"engine": "kernel"})
            self.assertEqual(r.status, 409)
            self.assertEqual(r.json()["code"], 3)
            doc = self.ok("POST", "/api/gw/awg/engine", {"engine": "kernel", "force": True})
            self.assertEqual(doc["engine"], "kernel")
            doc = self.ok("POST", "/api/gw/awg/engine", {"engine": "program"})
            self.assertTrue(doc["rolled_back"])
            self.assertEqual(doc["warnings"], ["клиенты не вернулись"])
            self.assertEqual(self.call("POST", "/api/gw/awg/engine", {"engine": "x"}).status, 400)
        self.assertEqual(calls, [("kernel", False, "person:panel"), ("kernel", True, "person:panel"),
                                 ("program", False, "person:panel")])

    def test_p3_export_and_import(self):
        gw(("awg2",))
        secrets.set("awg2.server.private", "SERVERKEY")
        secrets.set("bot.token", "NOT-EXPORTED")
        r = self.call("POST", "/api/gw/awg/export", {})
        self.assertEqual(r.status, 200)
        self.assertIn("no-store", r.headers["cache-control"])
        doc = r.json()["doc"]
        self.assertEqual(doc["secrets"], {"awg2.server.private": "SERVERKEY"})
        self.assertNotIn("NOT-EXPORTED", r.text)
        secrets.delete("awg2.server.private")
        res = self.ok("POST", "/api/gw/awg/import", {"doc": doc})
        self.assertEqual(res["inbounds"], ["awg2"])
        self.assertEqual(secrets.get("awg2.server.private"), "SERVERKEY")
        self.assertEqual(self.call("POST", "/api/gw/awg/import", {"doc": "x"}).status, 400)

    @unittest.skipUnless(shutil.which("node"), "нет node: функции app.js не запустить")
    def test_p4_the_page_reads_the_answers(self):
        answers = {}
        gw(("awg2",), engine={"pending": True})
        answers["offer"] = self.ok("GET", "/api/gw/awg/engine")
        gw(("awg2", "awg3"), engine={"pending": True})
        self.assertEqual(self.ok("GET", "/api/gw/awg/engine")["offer"]["blocked"], [])     # 3.1: the module's set
        leg = {"id": "e-0000000003", "kind": "awg", "iface": "kawg3", "version": "", "name": "", "host": "x",
               "where": "AmneziaWG · 198.51.100.3"}
        with mock.patch.object(exits, "pool", lambda runner=None, parser=None, roles=("abroad",): [dict(leg, version="3.1")]):
            self.assertEqual(self.ok("GET", "/api/gw/awg/engine")["offer"]["blocked"], [])   # a leg of 3.1: the set
        with mock.patch.object(exits, "pool", lambda runner=None, parser=None, roles=("abroad",): [leg]):
            answers["blocked"] = self.ok("GET", "/api/gw/awg/engine")                    # a leg of no known line
        gw(("awg2", "awg3"), engine={"want": "kernel", "force": True})
        kmod(True)                                     # a module without the set: the inbound 3.1 is mixing
        answers["warn"] = self.ok("GET", "/api/gw/awg/engine")
        gw(("awg2",), engine={"want": "kernel"})
        answers["none"] = self.ok("GET", "/api/gw/awg/engine")
        out = run_node(answers)
        self.assertEqual(out["offer"]["mode"], "offer")
        self.assertEqual(out["blocked"]["mode"], "blocked")
        self.assertIn("неизвестной линии", out["blocked"]["list"][0])
        self.assertEqual(out["warn"]["mode"], "warn")
        self.assertEqual(out["none"]["mode"], "none")
        self.assertEqual(out["offer"]["active"], "program")
        self.assertEqual(out["warn"]["active"], "kernel")


def run_node(answers):
    with open(os.path.join(STATIC, "app.js"), encoding="utf-8") as f:
        js = f.read()
    parts = [jsparts.const(js, n) for n in ("isObj", "arr")] + [jsparts.function(js, n) for n in ("normAwg", "awgOfferOf")]
    script = "%s\nconst A = %s;\nconst out = {};\nfor (const k of Object.keys(A)) { const a = normAwg(A[k]); " \
             "out[k] = Object.assign({ active: a.active }, awgOfferOf(a)); }\nconsole.log(JSON.stringify(out));\n" % (
                 "\n".join(parts), json.dumps(answers, ensure_ascii=False))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        res = subprocess.run(["node", path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    finally:
        os.unlink(path)
    if res.returncode != 0:
        raise AssertionError(res.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(res.stdout.decode("utf-8").strip().splitlines()[-1])
