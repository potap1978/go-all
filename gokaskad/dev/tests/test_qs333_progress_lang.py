"""Kaskad 3.3.3 — the progress of the wizard's jobs in the language of the page (owner, 2026-10-12: the English wizard
showed «Модуль ядра AmneziaWG», «Собираю модуль под ядро 6.8.0-48-generic (около минуты)…», «установлен · порт
51820», «ставлю…», «в очереди», «Шлюз», «включён» — snapshot k3/design/port-332/owner-2026-10-12-stage2-en-progress.png).

The rows of job.json were words of the language the job was started in. Now the job runs under i18n.keeping():
every tr()/trn() returns a Said (key + arguments), Progress keeps them next to the words (``title_m``, ``detail_m``,
the result's ``text_m``), and jobs.localize() says them again in the language of the request that reads job.json
(X-Kaskad-Lang of the page) — also when the language was switched while the job ran."""

import json
import os
import re
import unittest
from unittest import mock

from kaskad import i18n
from kaskad.errors import ConflictError
from kaskad.i18n import tr, trn
from kaskad.setup import jobs, model, steps
from tests.setup import test_setup_core as core

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYR = re.compile("[А-Яа-яЁё]")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class SaidTest(unittest.TestCase):
    def test_keeping_makes_tr_and_trn_said(self):
        with i18n.use_lang("ru"):
            self.assertNotIsInstance(tr("setup.steps.queued"), i18n.Said)      # outside a job: plain words
            with i18n.keeping():
                q, n = tr("setup.steps.queued"), trn("setup.steps.importForwardsDone", 3)
        self.assertIsInstance(q, i18n.Said)
        self.assertEqual(i18n.said_text(i18n.said_doc(q), "en"), tr("setup.steps.queued", "en"))
        self.assertEqual(i18n.said_text(i18n.said_doc(n), "en"), trn("setup.steps.importForwardsDone", 3, "en"))
        self.assertEqual(i18n.said_text(i18n.said_doc(n), "ru"), n)

    def test_glued_pieces_and_nested_arguments(self):
        with i18n.use_lang("ru"), i18n.keeping():
            glued = tr("setup.steps.installed") + tr("setup.steps.portSuffix", port=51820)
            joined = i18n.join("; ", [tr("setup.steps.moduleAsProgram"), "LXC"])
            nested = tr("setup.steps.undoFailed", problems=tr("setup.steps.moduleNoReason"))
        for said in (glued, joined, nested):
            doc = json.loads(json.dumps(i18n.said_doc(said)))                # job.json is JSON
            self.assertEqual(i18n.said_text(doc, "ru"), said)
            self.assertFalse(CYR.search(i18n.said_text(doc, "en").replace("LXC", "")), i18n.said_text(doc, "en"))
        self.assertIsNone(i18n.said_doc("plain words"))                     # nothing to say again

    def test_secrets_in_arguments_are_cleaned(self):
        with i18n.keeping():
            said = tr("setup.steps.undoFailed", problems="token=abc")
        doc = i18n.said_doc(said, lambda s: s.replace("abc", "***"))
        self.assertEqual(doc["a"]["problems"], "token=***")


class ProgressLangTest(core.RootCase):
    setUp, tearDown, password = core.StepsTest.setUp, core.StepsTest.tearDown, core.StepsTest.password

    def _run_protocols(self):
        """The step «protocols» as the job runs it: Russian server, keeping() on; the ops speak through tr()."""
        self.password()
        plan = [{"kind": k, "title": t} for k, t in (("wg", "WireGuard"), ("awg2", "AmneziaWG 2.0"),
                                                     ("l2tp", "L2TP/IPsec"))]

        def install(runner, kind, port=None, force=False, awg_engine=None, progress=None):
            if kind == "l2tp":
                raise ConflictError(tr("gw_ops.err.port_busy", port=1701, protos="udp", why="xl2tpd"))
            if kind == "awg2":
                progress(tr("gw_awgkmod.say.build", kernel="6.8.0-48-generic"))
                return {"ok": True, "record": {"port": 41023}, "awg_engine": "program",
                        "warnings": [tr("setup.steps.moduleAsProgram")]}
            return {"ok": True, "record": {"port": 51820}}
        docs = []

        class P(jobs.Progress):
            def _flush(self):
                docs.append({"items": [dict(r) for r in self.rows]})
        with mock.patch("kaskad.gw.ops.inbound_plan", lambda r, kinds, st=None: [p for p in plan if p["kind"] in kinds]), \
                mock.patch("kaskad.gw.ops.inbound_install", install), \
                mock.patch("kaskad.gw.ops.enable", lambda r: {"ok": True}), \
                i18n.use_lang("ru"), i18n.keeping():
            prog = P(None, "protocols")
            prog.job_id = "x"
            out = steps.run("protocols", {"kinds": ["l2tp", "wg", "awg2"]}, self.runner, prog)
        return docs, out

    def test_every_row_is_said_in_the_language_of_the_page(self):
        docs, _out = self._run_protocols()
        self.assertTrue(docs)
        for doc in docs:                                   # every moment of the job: queued, building, done
            ru = [r["title"] + " " + r["detail"] for r in doc["items"]]
            self.assertTrue(any(CYR.search(x) for x in ru))                  # written in Russian
            en = jobs.localize(doc, "en")
            for r in en["items"]:
                self.assertFalse(CYR.search(r["title"] + r["detail"]), r)
                self.assertNotIn("title_m", r)
                self.assertNotIn("detail_m", r)
            back = jobs.localize(doc, "ru")
            self.assertEqual([r["title"] + " " + r["detail"] for r in back["items"]], ru)

    def test_switch_while_the_job_runs(self):
        """job.json does not change; the reader's language does — each read says it in the language of now."""
        docs, _out = self._run_protocols()
        last = docs[-1]
        with i18n.use_lang("en"):
            words = " ".join(r["title"] + r["detail"] for r in jobs.localize(last)["items"])
        self.assertFalse(CYR.search(words), words)
        self.assertIn("51820", words)

    def test_result_text_is_said_again(self):
        with i18n.use_lang("ru"), i18n.keeping():
            res = jobs._keep_result({"ok": False, "state": "error", "text": tr("setup.jobs.interrupted")})
        doc = json.loads(json.dumps({"items": [], "result": res}))
        self.assertEqual(jobs.localize(doc, "en")["result"]["text"], tr("setup.jobs.interrupted", "en"))
        self.assertNotIn("text_m", jobs.localize(doc, "en")["result"])

    def _finished_job(self):
        """The job to its end as Progress.finish writes it — job.json on disk, read back as the page does after a
        reload (api state → jobs.localize(jobs.status()))."""
        docs, out = self._run_protocols()
        jobs.write({"id": "x", "step": "protocols", "state": "running", "items": docs[-1]["items"]})
        prog = jobs.Progress(None, "protocols")
        prog.job_id, prog.rows = "x", [dict(r) for r in docs[-1]["items"]]
        prog.finish(out)
        doc = jobs.status()
        self.assertEqual(doc["state"], "done")
        return doc

    @staticmethod
    def _walk(v, keys, words):
        if isinstance(v, dict):
            for k, x in v.items():
                keys.append(k)
                ProgressLangTest._walk(x, keys, words)
        elif isinstance(v, list):
            for x in v:
                ProgressLangTest._walk(x, keys, words)
        elif isinstance(v, str):
            words.append(v)

    def test_end_of_job_and_reload_in_the_language_of_the_page(self):
        """R-333 №1: after the end the page draws result.items, data.failed[].text — they were Russian again."""
        doc = self._finished_job()
        res = doc["result"]
        self.assertTrue(res["items"] and res["data"]["failed"])
        self.assertTrue(CYR.search(res["data"]["failed"][0]["text"]))           # written in Russian
        for read_in in (lambda: jobs.localize(jobs.status(), "en"), lambda: self._reload_en()):
            en = read_in()
            keys, words = [], []
            self._walk(en, keys, words)
            self.assertFalse([k for k in keys if k.endswith("_m")], keys)    # the keys do not go out
            r = en["result"]
            self.assertEqual(r["text"], tr("setup.steps.protocolsPartial", "en", done=2, total=3))
            for row in r["items"] + en["items"]:
                self.assertFalse(CYR.search(row["title"] + row["detail"]), row)
            failed = r["data"]["failed"][0]["text"]
            self.assertFalse(CYR.search(failed), failed)
            self.assertIn("1701", failed)
            self.assertNotIn("--force", failed)                              # R-333 №7: no CLI hint on the page
        ru = jobs.localize(jobs.status(), "ru")
        self.assertEqual(ru["result"]["data"]["failed"], doc["result"]["data"]["failed"])
        self.assertEqual([x["title"] for x in ru["result"]["items"]], [x["title"] for x in doc["result"]["items"]])

    def _reload_en(self):
        with i18n.use_lang("en"):                     # the language switched; the page reloads and asks again
            return jobs.localize(jobs.status())

    def test_warnings_and_error_of_the_result_are_said_again(self):
        with i18n.use_lang("ru"), i18n.keeping():
            res = jobs._keep_result({"ok": False, "state": "error", "text": "x", "items": [],
                                     "data": {"warnings": [tr("setup.steps.moduleAsProgram"), "plain words"]},
                                     "error": {"code": "e", "text": tr("setup.jobs.interrupted")}})
        doc = json.loads(json.dumps({"items": [], "result": res}, ensure_ascii=False))
        en = jobs.localize(doc, "en")["result"]
        self.assertEqual(en["data"]["warnings"], [tr("setup.steps.moduleAsProgram", "en"), "plain words"])
        self.assertEqual(en["error"]["text"], tr("setup.jobs.interrupted", "en"))
        self.assertNotIn("said_m", en)
        self.assertIn("said_m", doc["result"])                                # job.json itself is not changed

    def test_a_key_this_version_does_not_know_keeps_the_written_words(self):
        """R-333 №4: a job.json of another version (key renamed) — the words it was written with, not the key."""
        doc = {"items": [{"item": "a", "title": "Шлюз", "title_m": {"k": "setup.steps.goneInThisVersion", "a": {}},
                          "state": "ok", "detail": "включён · порт 1",
                          "detail_m": {"cat": [{"k": "setup.steps.gatewayOn", "a": {}}, " · ",
                                               {"k": "setup.steps.renamed", "a": {"port": 1}}]}}],
               "result": {"text": "Готово", "text_m": {"k": "setup.steps.renamedToo", "a": {}}}}
        en = jobs.localize(doc, "en")
        self.assertEqual(en["items"][0]["title"], "Шлюз")
        self.assertEqual(en["items"][0]["detail"], "включён · порт 1")
        self.assertEqual(en["result"]["text"], "Готово")
        self.assertNotIn("title_m", en["items"][0])

    def test_a_row_without_keys_stays_as_written(self):
        doc = {"items": [{"item": "x", "title": "xray", "state": "err", "detail": "exit status 1"}]}
        self.assertEqual(jobs.localize(doc, "en"), doc)


class WiringTest(unittest.TestCase):
    def test_the_job_runs_keeping_and_every_reader_localizes(self):
        cli = read("lib/kaskad/setup/cli.py")
        self.assertRegex(cli, r"i18n\.keeping\(\) if args\.job")
        web = read("lib/kaskad/setup/web.py")
        self.assertNotRegex(web, r"(?<!localize\()jobs\.status\(\)")       # api state, api job, the SSE event
        self.assertEqual(len(re.findall(r"jobs\.localize\(jobs\.status\(\)\)", web)), 3)

    def test_rows_of_refusals_keep_their_key(self):
        src = read("lib/kaskad/setup/steps.py")
        self.assertNotRegex(src, r"c\.say\([^\n]*util\.redact\(str\(e\)\)")
        self.assertNotRegex(src, r"c\.say\([^\n]*\"; \"\.join")


if __name__ == "__main__":
    unittest.main()
