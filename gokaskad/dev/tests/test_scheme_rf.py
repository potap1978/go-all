"""R4 — the tick ⚠РФ on the scheme (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.1, §9 «Схема (Б1)», «Рубеж 2»):

* the intent ``rules`` → ``ru_direct`` without ``confirm_not_rf``: the dry run writes nothing and stays valid —
  ``results[].ok`` true, ``results[].need`` the block (П13); the real apply — 409 with ``data.need`` in the BODY of
  the answer, ``state.json`` byte for byte, neither intent of a batch applied;
* with the tick — the consent ``via = scheme``, ``orgs`` of the shown block;
* the graph: ``profiles.rf`` (mode, required, the block); without the consent the card «Россия напрямую» is
  ``no_consent``; a chosen profile in the mode ``need`` is shown chosen, with the mark «не действует» in the text of
  the inheritance, and acts as «всё через выход» (``_applied_profile``)."""

import json

from kaskad import errors, state
from kaskad.panel import routes, scheme
from kaskad.panel.routes import HttpError
from kaskad.rules import rfgate
from tests.rules_helpers import seed_small
from tests.test_rules_rf_consent import RfCase, _all_orgs

RU_D1 = {"type": "rules", "device": "d-00000001", "profile": "ru_direct"}


class SchemeRfTest(RfCase):
    def apply(self, body):
        return scheme.apply(body, runner=self.w.runner)

    def refused(self, body):
        with self.assertRaises(HttpError) as cm:
            self.apply(body)
        resp = routes.error_response(cm.exception)
        return resp.status, json.loads(resp.body)

    def test_dry_run_says_need_and_writes_nothing(self):
        self.node(enabled=True)
        before = self.raw()
        doc = self.apply({"intents": [RU_D1], "dry_run": True})
        self.assertTrue(doc["valid"])
        r = doc["results"][0]
        self.assertTrue(r["ok"])                                         # П13: ok stays true
        self.assertEqual(r["need"]["need"], "rf_consent")
        self.assertEqual(r["need"]["orgs"], _all_orgs())
        self.assertIn("Meta", r["need"]["block"])
        self.assertEqual(doc["need"]["need"], "rf_consent")
        self.assertEqual(self.raw(), before)
        # a route intent next to it carries no need
        doc = self.apply({"intents": [{"type": "route", "device": "d-00000002", "via": "block"}, RU_D1],
                          "dry_run": True})
        self.assertNotIn("need", doc["results"][0])
        self.assertIn("need", doc["results"][1])
        # with the tick — no need
        doc = self.apply({"intents": [dict(RU_D1, confirm_not_rf=True, orgs=_all_orgs())], "dry_run": True})
        self.assertNotIn("need", doc["results"][0])
        self.assertIsNone(doc["need"])
        self.assertEqual(self.raw(), before)

    def test_apply_without_the_tick_is_409_with_the_block(self):
        self.node(enabled=True)
        before, n = self.raw(), len(self.w.runner.calls)
        status, body = self.refused({"intents": [{"type": "route", "device": "d-00000002", "via": "block"}, RU_D1]})
        self.assertEqual((status, body["code"]), (409, errors.CONFLICT))
        self.assertEqual(body["data"]["need"], "rf_consent")              # З7: data reaches the answer
        self.assertIn("Скрипт не используется на территории РФ", body["data"]["consent"])
        self.assertEqual(self.raw(), before)                              # neither intent of the batch
        self.assertEqual(self.w.runner.calls[n:], [])

    def test_a_tick_without_orgs_is_400(self):
        """П-4: the scheme always shows a block, its tick names it (``orgs``); without — 400, the dry run as well."""
        self.node(enabled=True)
        before = self.raw()
        for dry in (True, False):
            status, body = self.refused({"intents": [dict(RU_D1, confirm_not_rf=True)], "dry_run": dry})
            self.assertEqual((status, body["code"]), (400, errors.USAGE), dry)
        self.assertEqual(self.raw(), before)

    def test_apply_with_the_tick(self):
        self.node(enabled=True)
        doc = self.apply({"intents": [dict(RU_D1, confirm_not_rf=True, orgs=_all_orgs())]})
        self.assertTrue(doc["applied"])
        c = self.g()["rf_consent"]
        self.assertEqual((c["via"], c["who"], c["orgs"]), ("scheme", "admin", _all_orgs()))
        self.assertEqual(state.load()["gateway"]["devices"][0]["rules"], "ru_direct")
        self.assertEqual(doc["graph"]["profiles"]["rf"]["mode"], "full")
        self.assertFalse(doc["graph"]["profiles"]["rf"]["required"])

    def test_a_narrower_shown_block_is_refused(self):
        self.node(enabled=True)
        self.scan_with(["meta", "fbk"])
        before = self.raw()
        status, body = self.refused({"intents": [dict(RU_D1, confirm_not_rf=True, orgs=["meta"])]})
        self.assertEqual((status, body["data"]["orgs"]), (409, ["meta", "fbk"]))
        self.assertEqual(self.raw(), before)

    def test_bad_tick_is_400(self):
        self.node()
        for it in (dict(RU_D1, confirm_not_rf="yes"), dict(RU_D1, confirm_not_rf=True, orgs="meta")):
            with self.assertRaises(HttpError) as cm:
                self.apply({"intents": [it], "dry_run": True})
            self.assertEqual(cm.exception.status, 400, it)

    def test_the_graph_in_the_need_mode(self):
        """Рубеж 2: a state of gen 2 with ru_direct and no consent (a hand edit, a foreign copy) — chosen, not acting."""
        st = self.node(gen=2, default="ru_direct")
        seed_small()
        self.assertEqual(rfgate.mode(st), "need")
        g = scheme.build(st, [], {}, scheme.load_layout(), True, scheme.ru_state(st))
        rf = g["profiles"]["rf"]
        self.assertEqual((rf["mode"], rf["required"]), ("need", True))
        self.assertEqual(rf["need"]["need"], "rf_consent")
        card = next(n for n in g["nodes"] if n["id"] == "rules:ru_direct")
        self.assertEqual(card["data"]["ru"]["state"], "no_consent")
        self.assertEqual(g["profiles"]["default"], "ru_direct")           # shown chosen
        d1 = state.load()["gateway"]["devices"][0]
        self.assertEqual(scheme._applied_profile(st, d1, True), "all_exit")   # acts as «всё через выход»
        text = scheme._inherited_rules_text(st, d1, "device")
        self.assertIn("не действует: нет подтверждения ⚠РФ", text)
        # with the consent — acts, no mark
        st2 = json.loads(json.dumps(st))
        rfgate.grant(st2, "panel", "admin", "ru", ["meta"], log=[])
        self.assertEqual(scheme._applied_profile(st2, d1, True), "ru_direct")
        self.assertNotIn("⚠РФ", scheme._inherited_rules_text(st2, d1, "device"))
        self.assertEqual(scheme.ru_state(st2)["state"], "unknown")       # then the table of the kernel tells

    def test_current_graph_reads_the_consent(self):
        self.node(gen=2)
        seed_small()
        card = next(n for n in scheme.current_graph()["nodes"] if n["id"] == "rules:ru_direct")
        self.assertEqual(card["data"]["ru"]["state"], "no_consent")
        st = state.load()
        rfgate.grant(st, "panel", "admin", "ru", ["meta"], log=[])
        state.save(st)
        card = next(n for n in scheme.current_graph()["nodes"] if n["id"] == "rules:ru_direct")
        self.assertNotEqual(card["data"]["ru"]["state"], "no_consent")
