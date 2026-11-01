"""R4 — the tick ⚠РФ in the panel's HTTP API (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.1, §9 «Панель»):

* ``PUT /api/rules/profile`` without the tick where it is needed — 409 with ``data.need = "rf_consent"`` and the
  block in the BODY of the answer (``_http_error`` keeps ``data``); with ``confirm_not_rf`` and ``orgs`` — the consent
  ``via = panel``, ``who`` = the login of the panel, no address;
* ``ver`` of the form «Правила» (SPEC-stage1-exits §7.9 «Версия объекта»): ``GET /api/rules`` gives it; ``target:
  "default"`` without it — 400 ``ver_required``, with another — 409 ``object_changed``, nothing written; the switches
  do not change it;
* ``PUT /api/rules/consent`` — «Подтвердить» (legacy, need, uncovered) through ``_change`` with ``rf``;
* ``GET /api/rules`` → ``rf.need`` — the block the page shows (the owner's text).
Domains of the tests — example.com and invented ones."""

import json

from kaskad import errors, state
from kaskad.panel import routes
from kaskad.panel.routes import HttpError, Request
from kaskad.rules import rfgate
from tests.rules_helpers import seed_small
from tests.test_rules_rf_consent import RfCase, _all_orgs


class PanelRfCase(RfCase):
    def call(self, method, path, body=None):
        r, m = routes.find(method, path)
        self.assertIsNotNone(m, path)
        return r.handler(Request(method, path, body=json.dumps(body).encode() if body is not None else b"", match=m))

    def refused(self, method, path, body):
        """The answer the browser gets for a refusal: the status and the JSON body of ``routes.error_response``."""
        with self.assertRaises(HttpError) as cm:
            self.call(method, path, body)
        resp = routes.error_response(cm.exception)
        return resp.status, json.loads(resp.body)

    def ver(self):
        return json.loads(self.call("GET", "/api/rules").body)["ver"]


class ProfileTest(PanelRfCase):
    def test_default_without_the_tick_is_409_with_the_block_in_the_body(self):
        self.node(enabled=True)
        before, n = self.raw(), len(self.w.runner.calls)
        status, body = self.refused("PUT", "/api/rules/profile", {"target": "default", "profile": "ru_direct",
                                                                  "ver": self.ver()})
        self.assertEqual((status, body["code"]), (409, errors.CONFLICT))
        d = body["data"]
        self.assertEqual(d["need"], "rf_consent")
        self.assertEqual(d["orgs"], _all_orgs())
        self.assertIn("Meta", d["block"])
        self.assertIn("признана экстремистской", d["block"])
        self.assertEqual(d["consent"], "Скрипт не используется на территории РФ")
        self.assertEqual(self.raw(), before)                      # nothing written
        self.assertEqual(self.w.runner.calls[n:], [])             # nothing ran (С6)

    def test_default_with_the_tick_writes_the_consent_of_the_panel(self):
        self.node(enabled=True)
        resp = self.call("PUT", "/api/rules/profile", {"target": "default", "profile": "ru_direct", "ver": self.ver(),
                                                        "confirm_not_rf": True, "orgs": _all_orgs()})
        self.assertEqual(resp.status, 200)
        doc = json.loads(resp.body)
        self.assertEqual(doc["rules"]["default"], "ru_direct")
        self.assertEqual(doc["status"]["rf"]["mode"], "full")
        c = self.g()["rf_consent"]
        self.assertEqual((c["via"], c["who"]), ("panel", "admin"))       # the login of the panel (one admin)
        self.assertNotIn("203.0.113", json.dumps(c))                     # no address of anybody
        self.assertNotEqual(doc["ver"], None)
        self.assertEqual(doc["ver"], self.ver())                         # the new ver of the form

    def test_a_device_and_a_person(self):
        self.node(enabled=True)
        for body in ({"target": "device", "id": "d-00000002", "profile": "ru_direct"},
                     {"target": "user", "id": "u-00000002", "profile": "ru_direct"}):
            status, ans = self.refused("PUT", "/api/rules/profile", body)
            self.assertEqual((status, ans["data"]["need"]), (409, "rf_consent"), body)
        self.call("PUT", "/api/rules/profile", {"target": "device", "id": "d-00000002", "profile": "ru_direct",
                                                "confirm_not_rf": True, "orgs": _all_orgs()})
        self.assertEqual(self.g()["rf_consent"]["via"], "panel")
        # with the consent there: another device needs no tick (no ver for people and devices)
        self.call("PUT", "/api/rules/profile", {"target": "device", "id": "d-00000001", "profile": "ru_direct"})
        self.assertEqual(state.load()["gateway"]["devices"][0]["rules"], "ru_direct")

    def test_the_block_widened_while_reading(self):
        """§3.5: the page showed «Meta», the lists now mark Meta and ФБК — 409 again with the new block."""
        self.node(enabled=True)
        self.scan_with(["meta", "fbk"])
        before = self.raw()
        status, body = self.refused("PUT", "/api/rules/profile", {"target": "default", "profile": "ru_direct",
                                                                  "ver": self.ver(), "confirm_not_rf": True,
                                                                  "orgs": ["meta"]})
        self.assertEqual((status, body["data"]["need"]), (409, "rf_consent"))
        self.assertEqual(body["data"]["orgs"], ["meta", "fbk"])
        self.assertIn("изменился", body["error"])
        self.assertEqual(self.raw(), before)

    def test_bad_ticks_are_400(self):
        self.node()
        for extra in ({"confirm_not_rf": "yes"}, {"confirm_not_rf": True}, {"confirm_not_rf": True, "orgs": "meta"},
                      {"confirm_not_rf": True, "orgs": [1]}, {"confirm_not_rf": True, "orgs": ["x" * 65]}):
            body = dict({"target": "default", "profile": "ru_direct", "ver": self.ver()}, **extra)
            status, ans = self.refused("PUT", "/api/rules/profile", body)
            self.assertEqual(status, 400, extra)
        self.assertNotIn("rf_consent", self.g())

    def test_a_page_tick_names_the_block(self):
        """П-4 (REV-s1-t7-r45-sec): the panel always shows a block, so its tick carries ``orgs``; without them — 400
        on every page route (the profile, a device, «Подтвердить»): the consent to «the block as it is now» is the
        CLI's (§5.1). Nothing is written."""
        self.node(gen=1, enabled=True)                                # legacy: «Подтвердить» has something to grant
        before = self.raw()
        for path, body in (("/api/rules/profile", {"target": "default", "profile": "ru_direct", "ver": self.ver()}),
                           ("/api/rules/profile", {"target": "device", "id": "d-00000002", "profile": "ru_direct"}),
                           ("/api/rules/consent", {})):
            status, ans = self.refused("PUT", path, dict(body, confirm_not_rf=True))
            self.assertEqual((status, ans["code"]), (400, errors.USAGE), path)
            self.assertIn("orgs", ans["error"])
        self.assertEqual(self.raw(), before)
        # the CLI keeps its consent without a list (ops, not HTTP)
        from kaskad.rules import ops as rops
        rops.consent(self.w.runner, rf={"confirm": True, "via": "cli"})
        self.assertEqual(self.g()["rf_consent"]["via"], "cli")


class VerTest(PanelRfCase):
    def test_ver_of_the_form(self):
        self.node(enabled=True)
        ver = self.ver()
        before = self.raw()
        status, body = self.refused("PUT", "/api/rules/profile", {"target": "default", "profile": "all_exit"})
        self.assertEqual((status, body["data"]["code"]), (400, "ver_required"))
        self.assertIn("обновите страницу", body["error"])
        status, body = self.refused("PUT", "/api/rules/profile", {"target": "default", "profile": "ru_direct",
                                                                  "ver": "0" * 12, "confirm_not_rf": True,
                                                                  "orgs": _all_orgs()})
        self.assertEqual((status, body["data"]["code"], body["data"]["ver"]), (409, "object_changed", ver))
        self.assertEqual(self.raw(), before)                       # neither the profile nor the consent
        self.call("PUT", "/api/rules/blocks", {"ads": False})       # the switches are not in the object
        self.assertEqual(self.ver(), ver)
        # the profile changed elsewhere (CLI, bot): the old ver of the tab is refused
        from kaskad.rules import ops as rops
        rops.set_default(self.w.runner, "ru_direct", rf={"confirm": True, "via": "cli"})
        status, body = self.refused("PUT", "/api/rules/profile", {"target": "default", "profile": "all_exit",
                                                                  "ver": ver})
        self.assertEqual((status, body["data"]["code"]), (409, "object_changed"))
        self.assertEqual(state.load()["gateway"]["rules"]["default"], "ru_direct")


class ConsentTest(PanelRfCase):
    def test_confirm_in_the_legacy_mode(self):
        self.node(gen=1, enabled=True)
        self.assertEqual(json.loads(self.call("GET", "/api/rules").body)["rf"]["mode"], "legacy")
        status, body = self.refused("PUT", "/api/rules/consent", {})
        self.assertEqual((status, body["data"]["need"]), (409, "rf_consent"))
        doc = json.loads(self.call("PUT", "/api/rules/consent", {"confirm_not_rf": True, "orgs": _all_orgs()}).body)
        self.assertTrue(doc["granted"])
        self.assertEqual(doc["status"]["rf"]["mode"], "full")
        g = self.g()
        self.assertEqual(g["rf_consent"]["via"], "panel")
        self.assertNotIn("rf_legacy", g)

    def test_confirm_in_the_need_mode(self):
        """A copy of another server (or a hand edit): ru_direct chosen, not acting — «Подтвердить» turns it on."""
        st = self.node(gen=2, default="ru_direct")
        seed_small()                                                 # the lists are there: only the consent lacks
        self.assertEqual(rfgate.mode(st), "need")
        doc = json.loads(self.call("GET", "/api/rules").body)
        self.assertEqual((doc["rf"]["mode"], doc["default_applied"], doc["default_why"]),
                         ("need", "all_exit", "no_consent"))
        self.call("PUT", "/api/rules/consent", {"confirm_not_rf": True, "orgs": _all_orgs()})
        self.assertEqual(rfgate.mode(state.load()), "full")

    def test_nothing_chosen_writes_nothing(self):
        self.node()
        doc = json.loads(self.call("PUT", "/api/rules/consent", {"confirm_not_rf": True, "orgs": _all_orgs()}).body)
        self.assertFalse(doc["granted"])
        self.assertNotIn("rf_consent", self.g())                    # no consent to nothing (§3.4)


class StatusTest(PanelRfCase):
    def test_get_rules_carries_the_block(self):
        self.node()
        self.scan_with(["meta"])
        rf = json.loads(self.call("GET", "/api/rules").body)["rf"]
        self.assertEqual(rf["need"]["need"], "rf_consent")
        self.assertEqual(rf["need"]["orgs"], ["meta"])
        self.assertEqual(rf["need"]["block"],
                         "⚠ Данная организация признана экстремистской на территории Российской Федерации (Meta)")
        self.assertEqual(rf["need"]["consent"], "Скрипт не используется на территории РФ")
        self.assertEqual(rf["marked"]["force"][0]["org"], "meta")
        self.assertTrue(rf["required"])
