"""Q6 (SPEC-stage1-exit-groups §8.2–8.3, §8.9): API панели для «Российских сайтов».

* ``PUT /api/rules/ru-via`` {items: [{user|device, via}]} — диалог «Выбрать людей» и поле пользователя или
  устройства: одной записью, всё или ничего, без ``rev``; только id (u-…/d-…), группа — российская; у строки — ``ver``
  её выбора (``ru_ver`` из ``GET /api/rules``, REV-3310 П1): нет — 400 ``ver_required``, другое — 409 ``object_changed``;
* ``GET /api/gw/people`` — у пользователей и устройств ``ru_via`` (своё значение), ``ru`` ({via, from}) и ``ru_net``
  ({step: direct | exit | last_direct | last_block | none, group, since, exit}) — только файлы;
* live.json: последний шаг российской группы — в ``no_exit_ru``, а не в ``no_exit``: у устройства, чья российская
  группа на последнем шаге, зарубежные сайты работают — это не «нет выхода в интернет» (§8.3)."""

import json
import os
import time

from kaskad import paths, snapshot, state
from kaskad.gw import live, model, policy_ops
from kaskad.gw import policy as P
from kaskad.panel import api as _api                                      # noqa: F401 — the routes of the panel
from kaskad.panel import routes
from kaskad.panel.routes import HttpError, Request
from kaskad.rules import panel as _rules_panel                            # noqa: F401 — /api/rules/*
from tests.gw.test_ru_target import D1, D3, HOME, RuWorld


class ApiCase(RuWorld):
    def setUp(self):
        super().setUp()
        self.world(via=None)
        st = state.load()
        self.uid = st["gateway"]["users"][0]["id"]
        snapshot.forget()

    def call(self, method, path, body=None):
        r, m = routes.find(method, path)
        self.assertIsNotNone(m, path)
        resp = r.handler(Request(method, path, body=json.dumps(body).encode() if body is not None else b"", match=m))
        return json.loads(resp.body)

    def refused(self, method, path, body):
        with self.assertRaises(HttpError) as cm:
            self.call(method, path, body)
        return cm.exception.status

    def it(self, kind, ref, via):
        """A row of the dialog: the ``ver`` of the row as ``GET /api/rules`` gives it (what a page has read)."""
        snapshot.forget()
        doc = self.call("GET", "/api/rules")
        row = [x for x in doc[kind + "s"] if x["id"] == ref][0]
        return {kind: ref, "via": via, "ver": row["ru_ver"]}

    def own(self, kind, ref):
        st = state.load()
        obj = model.find_user(st, ref) if kind == "user" else model.find_device(st, ref)
        return obj.get("ru_via")

    def write_live(self, no_exit=None, no_exit_ru=None):
        doc = {"v": live.V, "ts": time.time(), "seq": 1, "no_exit": no_exit or {}, "no_exit_ru": no_exit_ru or {},
               "events": [], "exits": {}, "devices": {}, "policy": {"node_offline": False}}
        os.makedirs(os.path.dirname(paths.live_json()), exist_ok=True)
        with open(paths.live_json(), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        snapshot.forget()

    def write_pins(self, groups):
        os.makedirs(os.path.dirname(paths.pins_json()), exist_ok=True)
        with open(paths.pins_json(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "saved": int(time.time()), "groups": groups}, f)
        snapshot.forget()


class PutRuViaTest(ApiCase):
    def test_several_in_one_write(self):
        ans = self.call("PUT", "/api/rules/ru-via", {"items": [self.it("user", self.uid, HOME),
                                                                self.it("device", D3, "direct")]})
        self.assertTrue(ans["ok"])
        self.assertEqual(ans["changed"], 2)
        self.assertEqual((self.own("user", self.uid), self.own("device", D3)), (HOME, "direct"))
        grp = [g for g in ans["status"]["ru_groups"] if g["id"] == HOME][0]
        self.assertEqual(grp["users"], [self.uid])
        self.assertIn(D1, grp["devices"])
        self.assertNotIn(D3, grp["devices"])
        self.assertEqual(ans["warnings"], [])

    def test_inherit_removes_the_own_value(self):
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("device", D1, HOME)]})
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("device", D1, "inherit")]})
        self.assertIsNone(self.own("device", D1))

    def test_all_or_nothing(self):
        status = self.refused("PUT", "/api/rules/ru-via", {"items": [self.it("user", self.uid, HOME),
                                                                     self.it("device", D1, P.MAIN)]})
        self.assertEqual(status, 400)                                 # a foreign group is never «Российские сайты»
        self.assertIsNone(self.own("user", self.uid))

    def test_bad_bodies(self):
        v = self.it("device", D1, HOME)["ver"]
        for body in ({}, {"items": []}, {"items": "x"}, {"items": [{"user": self.uid, "ver": v}]},
                     {"items": [{"user": self.uid, "device": D1, "via": HOME, "ver": v}]},
                     {"items": [{"user": self.uid, "via": HOME, "ver": v, "extra": 1}]},
                     {"items": [{"user": "Мама", "via": HOME, "ver": v}]},       # a name could be another person
                     {"items": [{"device": D1, "via": 5, "ver": v}]},
                     {"items": [{"device": D1, "via": "g-ffffff", "ver": v}]},
                     {"items": [{"device": D1, "via": "group:" + HOME, "ver": v}]},
                     {"items": [{"device": D1, "via": HOME, "ver": v}] * 2001}):
            self.assertEqual(self.refused("PUT", "/api/rules/ru-via", body), 400, str(body)[:80])
        self.assertIsNone(self.own("device", D1))
        self.assertEqual(self.refused("PUT", "/api/rules/ru-via",
                                      {"items": [{"device": "d-ffffffff", "via": HOME, "ver": v}]}), 404)

    def test_no_consent_is_asked_or_given(self):
        before = state.load()["gateway"].get("rf_consent")
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("user", self.uid, HOME)]})
        self.assertEqual(state.load()["gateway"].get("rf_consent"), before)


class RuViaVerTest(ApiCase):
    """REV-3310 П1: two windows never overwrite each other's choice silently — ``ver`` of each row, as the route."""

    def refused_with(self, body):
        with self.assertRaises(HttpError) as cm:
            self.call("PUT", "/api/rules/ru-via", body)
        return cm.exception

    def test_every_row_of_get_rules_has_its_ver(self):
        doc = self.call("GET", "/api/rules")
        for row in doc["users"] + doc["devices"]:
            self.assertRegex(row["ru_ver"], r"^[0-9a-f]{12}$", row["id"])

    def test_a_row_without_ver_is_an_old_tab(self):
        e = self.refused_with({"items": [{"user": self.uid, "via": HOME}]})
        self.assertEqual((e.status, (e.data or {}).get("code")), (400, "ver_required"))
        e = self.refused_with({"items": [self.it("device", D1, "direct"), {"user": self.uid, "via": HOME, "ver": ""}]})
        self.assertEqual((e.status, (e.data or {}).get("code")), (400, "ver_required"))
        self.assertEqual((self.own("user", self.uid), self.own("device", D1)), (None, None))

    def test_two_windows_on_the_same_person(self):
        first, second = self.it("user", self.uid, HOME), self.it("user", self.uid, "direct")   # both read «inherit»
        self.assertEqual(self.call("PUT", "/api/rules/ru-via", {"items": [first]})["changed"], 1)
        e = self.refused_with({"items": [self.it("device", D3, "direct"), second]})
        self.assertEqual(e.status, 409)
        self.assertEqual((e.data["code"], e.data["id"], e.data["target"]), ("object_changed", self.uid, "user"))
        self.assertEqual(e.data["ver"], self.it("user", self.uid, HOME)["ver"])
        self.assertEqual((self.own("user", self.uid), self.own("device", D3)), (HOME, None))   # nothing written
        self.assertEqual(self.call("PUT", "/api/rules/ru-via",
                                   {"items": [self.it("user", self.uid, "direct")]})["changed"], 1)  # read again

    def test_another_person_changed_meanwhile_is_not_a_conflict(self):
        mine = self.it("user", self.uid, HOME)
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("device", D3, "direct")]})   # another window
        self.assertEqual(self.call("PUT", "/api/rules/ru-via", {"items": [mine]})["changed"], 1)
        self.assertEqual((self.own("user", self.uid), self.own("device", D3)), (HOME, "direct"))

    def test_the_cli_writes_without_ver(self):
        from kaskad import sysrun
        from kaskad.rules import ops
        out = ops.set_ru_via(sysrun.default_runner(), "device", D1, HOME)
        self.assertEqual((out["ru_via"], out["changed"]), (HOME, True))

    def test_the_dialog_sends_the_ver_of_its_rows(self):
        js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "lib", "kaskad", "panel", "static", "app.js"), encoding="utf-8").read()
        self.assertIn("({ user: id, via: draft[id], ver: vers[id] })", js)
        self.assertIn('e.data.code === "object_changed"', js[js.index("function ruPickDialog"):][:9000])


class PeopleTest(ApiCase):
    def people(self):
        snapshot.forget()
        doc = self.call("GET", "/api/gw/people")
        return dict((d["id"], d) for d in doc["devices"]), dict((u["id"], u) for u in doc["users"])

    def test_direct_by_default(self):
        devs, users = self.people()
        d = devs[D1]
        self.assertEqual((d["ru_via"], d["ru"]["via"], d["ru_net"]["step"]), ("inherit", "direct", "direct"))
        self.assertEqual(users[self.uid]["ru_via"], "inherit")
        self.assertIsNone(users[self.uid]["ru_net"])

    def test_through_the_group_pinned_and_not_yet(self):
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("user", self.uid, HOME)]})
        self.write_pins({HOME: {"pins": {D1: {"exit": self.ru_ids[0], "tier": "t1", "since": 1, "why": "new"}}}})
        devs, users = self.people()
        self.assertEqual(devs[D1]["ru"], {"via": HOME, "from": "user"})
        self.assertEqual((devs[D1]["ru_net"]["step"], devs[D1]["ru_net"]["exit"]), ("exit", self.ru_ids[0]))
        self.assertEqual(devs[D3]["ru_net"]["step"], "none")          # no place yet (or «Россия напрямую» off)
        self.assertEqual((users[self.uid]["ru_via"], users[self.uid]["ru_net"]["step"]), (HOME, "exit"))

    def test_the_russian_last_step_is_not_no_exit(self):
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("user", self.uid, HOME)]})
        since = int(time.time()) - 60
        self.write_live(no_exit_ru={D1: {"step": "last_block", "group": HOME, "since": since}})
        devs, users = self.people()
        self.assertEqual((devs[D1]["ru_net"]["step"], devs[D1]["ru_net"]["since"]), ("last_block", since))
        self.assertNotIn((devs[D1].get("net") or {}).get("step"), ("last_block", "last_direct"))
        self.assertEqual(users[self.uid]["ru_net"]["step"], "last_block")

    def test_an_entry_of_another_group_is_not_taken(self):
        self.call("PUT", "/api/rules/ru-via", {"items": [self.it("device", D1, HOME)]})
        self.write_live(no_exit_ru={D1: {"step": "last_block", "group": "g-ffffff", "since": 1}})
        devs, _ = self.people()
        self.assertEqual(devs[D1]["ru_net"]["step"], "none")


class LiveNoExitTest(ApiCase):
    def dec(self):
        pol = P.load(state.load())
        return {"policy": pol, "groups": {
            P.MAIN: {"steps": {"d-00000002": "last_block"}, "last_since": 100},
            HOME: {"steps": {D1: "last_direct", D3: "exit"}, "last_since": 200}}}

    def test_two_maps(self):
        dec = self.dec()
        self.assertEqual(live.no_exit(dec), {"d-00000002": {"step": "last_block", "group": P.MAIN, "since": 100}})
        self.assertEqual(live.no_exit(dec, ru=True), {D1: {"step": "last_direct", "group": HOME, "since": 200}})

    def test_one_device_in_both(self):
        dec = self.dec()
        dec["groups"][P.MAIN]["steps"][D1] = "last_block"
        self.assertEqual(live.no_exit(dec)[D1]["group"], P.MAIN)
        self.assertEqual(live.no_exit(dec, ru=True)[D1]["group"], HOME)

    def test_without_a_policy_every_group_is_foreign(self):
        dec = self.dec()
        dec.pop("policy")
        self.assertEqual(set(live.no_exit(dec)), {"d-00000002", D1})
        self.assertEqual(live.no_exit(dec, ru=True), {})

    def test_no_pool_words_ignore_a_russian_group(self):
        dec = self.dec()
        dec["groups"][P.MAIN]["steps"] = {}
        dec["groups"][HOME]["last_step"] = "direct"
        dec["groups"][P.MAIN]["last_step"] = "block"
        self.assertEqual(live.no_pool_key(dec), "gw_live.no_pool")

    def test_groups_count_each_role_from_its_map(self):
        self.write_live(no_exit={"d-00000002": {"step": "last_block", "group": P.MAIN, "since": 100}},
                        no_exit_ru={D1: {"step": "last_direct", "group": HOME, "since": 200}})
        doc = policy_ops.groups()
        by = dict((g["id"], g) for g in doc["groups"])
        self.assertEqual((by[P.MAIN]["no_exit"], by[HOME]["no_exit"], by[HOME]["no_exit_since"]), (1, 1, 200))
        self.assertEqual(doc["no_exit"], 1)                           # «нет выхода в интернет» — foreign only
