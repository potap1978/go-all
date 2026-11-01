"""T15 Q1 — the model ``ru_via`` (docs/k3/SPEC-stage1-exit-groups.md §2.5, §8.5, §13.1 «model.py (Q1)»): the Russian
group of «Россия напрямую» of a person and of a device — ``parse_ru_via``, ``set_ru_via``, ``ru_via_of`` (device ->
person -> ``direct``; a reference to a group that is not there — ``direct``, never the next level), the cleaning at
«Удалить группу», ``kaskad.rules.ops.set_ru_via`` through ``gw/ops._change`` (no consent ⚠РФ asked or given) and the
fields of ``rules.ops.status()`` (``ru_groups``). Each point of §2.5 — a test; on the code before Q1 they are red
(no such functions, the deletion of a group did not see ``ru_via``)."""

import copy
import json
import random
import unittest
from unittest import mock

from kaskad import errors, snapshot, state
from kaskad.errors import KaskadError
from kaskad.gw import exits, model, policy as P, xray
from kaskad.i18n import tr
from kaskad.rules import ops as rops
from kaskad.rules import rfgate
from tests.gw import test_ru_invariant as inv
from tests.panel.test_panel_policy import PolicyCase
from tests.rules_helpers import consented, seed_small
from tests.test_rules_gw import RulesGwCase

HOME, OFFICE, STREAM = "g-abcdef", "g-0ff1ce", "g-123456"


def with_groups(st):
    """``st`` with the policy: «main», a foreign «Стриминг», Russian «Дом» and «Офис» (no exits — none is needed)."""
    g = model.gw(st)
    doc = P.default_doc("compat")
    doc["groups"].append(P.default_group(STREAM, "abroad", "compat", name="Стриминг"))
    doc["groups"].append(P.default_group(HOME, "ru", "ru_home", name="Дом"))
    doc["groups"].append(P.default_group(OFFICE, "ru", "ru_home", name="Офис"))
    g["exit_policy"] = doc
    return st


def plain():
    st = with_groups(state.empty())
    u = model.add_user(st, "Мама")
    d = model.add_device(st, u["id"], "Телефон", access=("wg",))
    d2 = model.add_device(st, u["id"], "Ноутбук", access=("wg",))
    return st, u, d, d2


class ParseTest(unittest.TestCase):
    def test_values(self):
        st, _u, _d, _d2 = plain()
        for v in (None, "", "inherit", " inherit "):
            self.assertEqual(model.parse_ru_via(st, v), "inherit", repr(v))
        self.assertEqual(model.parse_ru_via(st, "direct"), "direct")
        self.assertEqual(model.parse_ru_via(st, HOME), HOME)
        self.assertEqual(model.parse_ru_via(st, " %s " % OFFICE), OFFICE)

    def test_refusals(self):
        st, _u, _d, _d2 = plain()
        cases = {STREAM: "gw_model.err.ru_via_abroad", "main": "gw_model.err.ru_via_abroad",
                 "g-999999": "gw_model.err.ru_via_no_group",
                 "group:" + HOME: "gw_model.err.ru_via_bad", "Дом": "gw_model.err.ru_via_bad",
                 "block": "gw_model.err.ru_via_bad", "pool": "gw_model.err.ru_via_bad",
                 "exit:e-0000000001": "gw_model.err.ru_via_bad", "G-ABCDEF": "gw_model.err.ru_via_bad"}
        for value, key in cases.items():
            with self.assertRaises(KaskadError, msg=value) as cm:
                model.parse_ru_via(st, value)
            self.assertEqual(cm.exception.code, errors.USAGE, value)
            word = {"gw_model.err.ru_via_abroad": "зарубежная", "gw_model.err.ru_via_no_group": "нет",
                    "gw_model.err.ru_via_bad": "inherit"}[key]
            self.assertIn(word, str(cm.exception), value)
        with self.assertRaises(KaskadError) as cm:
            model.parse_ru_via(st, STREAM)
        self.assertIn("Стриминг", str(cm.exception))            # the name of the group, not its id
        with self.assertRaises(KaskadError) as cm:
            model.parse_ru_via(st, "main")
        self.assertIn("Основная", str(cm.exception))

    def test_no_policy_stored_has_no_russian_group(self):
        st = state.empty()
        self.assertEqual(model.parse_ru_via(st, "direct"), "direct")
        for v in (HOME, "main"):
            with self.assertRaises(KaskadError):
                model.parse_ru_via(st, v)

    def test_texts_both_languages(self):
        for key, kw in (("gw_model.err.ru_via_bad", {"value": "'x'"}), ("gw_model.err.ru_via_no_group", {"group": "g"}),
                        ("gw_model.err.ru_via_abroad", {"group": "g"}), ("rules.ru_via_items", {}),
                        ("rules.ru_via_target", {"target": "'x'", "kinds": "user|device"})):
            ru, en = tr(key, "ru", **kw), tr(key, "en", **kw)
            self.assertNotEqual(ru, key)
            self.assertNotEqual(en, key)
            self.assertFalse(any("а" <= ch <= "я" for ch in en.lower()), en)
            self.assertNotIn(".ru", ru + en)
        for lang in ("ru", "en"):                           # the refusal lists every form the model takes
            text = tr("gw_model.err.ru_via_bad", lang, value="'x'")
            for form in ("inherit", "direct"):
                self.assertIn(form, text)


class SetTest(unittest.TestCase):
    def test_kept_in_the_object_next_to_route_and_rules(self):
        st, u, d, _d2 = plain()
        self.assertEqual(model.set_ru_via(st, u, HOME), HOME)
        self.assertEqual(model.set_ru_via(st, d, "direct"), "direct")
        self.assertEqual((u["ru_via"], d["ru_via"]), (HOME, "direct"))
        self.assertNotIn("ru_via", model.peek(st).get("rules") or {})
        model.set_rules_conf(st, default="all_exit", block_ads=False)   # «Правила» write three keys only (RF §1)
        self.assertEqual(sorted(st["gateway"]["rules"]), ["block_ads", "block_torrent", "default"])
        self.assertEqual(u["ru_via"], HOME)
        self.assertEqual(model.set_ru_via(st, d, "inherit"), "inherit")
        self.assertNotIn("ru_via", d)                       # inherit: no field
        model.set_ru_via(st, d, None)
        self.assertNotIn("ru_via", d)

    def test_a_refusal_writes_nothing(self):
        st, u, _d, _d2 = plain()
        model.set_ru_via(st, u, HOME)
        before = copy.deepcopy(st)
        for bad in (STREAM, "g-999999", "x"):
            with self.assertRaises(KaskadError):
                model.set_ru_via(st, u, bad)
        self.assertEqual(st, before)


class OfTest(unittest.TestCase):
    def test_inheritance_device_person_direct(self):
        st, u, d, d2 = plain()
        self.assertEqual(model.ru_via_of(st, d), {"via": "direct", "from": "default"})   # the node: always direct (В4)
        model.set_ru_via(st, u, HOME)
        self.assertEqual(model.ru_via_of(st, d), {"via": HOME, "from": "user"})
        model.set_ru_via(st, d, "direct")                   # the device's own «direct» over the person's group
        self.assertEqual(model.ru_via_of(st, d), {"via": "direct", "from": "device"})
        self.assertEqual(model.ru_via_of(st, d2), {"via": HOME, "from": "user"})
        model.set_ru_via(st, d2, OFFICE)
        self.assertEqual(model.ru_via_of(st, d2), {"via": OFFICE, "from": "device"})
        model.set_ru_via(st, u, "direct")
        self.assertEqual(model.ru_via_of(st, d2), {"via": OFFICE, "from": "device"})
        d2["ru_via"] = "inherit"                            # a stored «inherit» (a hand edit) — the next level
        self.assertEqual(model.ru_via_of(st, d2), {"via": "direct", "from": "user"})

    def test_person_by_name(self):
        st, u, d, _d2 = plain()
        model.set_ru_via(st, u, HOME)
        d["user"] = "Мама"                                  # an import or a hand edit names the person
        self.assertEqual(model.ru_via_of(st, d), {"via": HOME, "from": "user"})

    def test_a_reference_to_a_deleted_group_is_direct_not_the_next_level(self):
        st, u, d, _d2 = plain()
        model.set_ru_via(st, u, OFFICE)
        model.set_ru_via(st, d, HOME)
        doc = st["gateway"]["exit_policy"]
        doc["groups"] = [g for g in doc["groups"] if g["id"] != HOME]          # gone at an import
        self.assertEqual(model.ru_via_of(st, d), {"via": "direct", "from": "device", "broken": HOME})
        st["gateway"].pop("exit_policy")                    # no policy at all (a rollback of the file)
        self.assertEqual(model.ru_via_of(st, d), {"via": "direct", "from": "device", "broken": HOME})

    def test_a_broken_value_is_direct(self):
        st, u, d, _d2 = plain()
        model.set_ru_via(st, u, HOME)
        for bad in (STREAM, "main", "group:" + HOME, "x", 5, {"via": HOME}, ["g"]):
            d["ru_via"] = bad
            got = model.ru_via_of(st, d)
            self.assertEqual((got["via"], got["from"]), ("direct", "device"), repr(bad))
            self.assertEqual(got["broken"], bad)

    def test_an_empty_string_inherits(self):
        """REV-s1-q1 №4: ``ru_via: ""`` (a hand edit, an import) — inherit, as ``parse_ru_via`` reads it."""
        st, u, d, _d2 = plain()
        model.set_ru_via(st, u, HOME)
        d["ru_via"] = ""
        self.assertEqual(model.ru_via_of(st, d), {"via": HOME, "from": "user"})

    def test_the_route_is_untouched(self):
        """The Russian group is not a route: ``route_of`` does not read ``ru_via``; a route to a Russian group is still
        refused (T2) and the form of the route still takes no Russian group."""
        st, u, d, _d2 = plain()
        before = model.route_of(st, d)
        model.set_ru_via(st, u, HOME)
        model.set_ru_via(st, d, OFFICE)
        self.assertEqual(model.route_of(st, d), before)
        with self.assertRaises(KaskadError):
            model.set_route(d, "group:" + HOME, st=st)
        self.assertEqual(model.group_of_route({"via": "pool"}), "main")


class RefsTest(unittest.TestCase):
    def test_refs_and_reassign(self):
        st, u, d, d2 = plain()
        u2 = model.add_user(st, "Дача")
        model.set_ru_via(st, u, HOME)
        model.set_ru_via(st, d, HOME)
        model.set_ru_via(st, d2, OFFICE)
        model.set_ru_via(st, u2, "direct")
        self.assertEqual(model.ru_via_refs(st, HOME), {"users": [u["id"]], "devices": [d["id"]]})
        self.assertEqual(model.ru_via_refs(st, OFFICE), {"users": [], "devices": [d2["id"]]})
        self.assertEqual(model.ru_via_refs(st, STREAM), {"users": [], "devices": []})
        for bad in ("inherit", None, HOME, STREAM, "g-999999"):        # an explicit choice, never «inherit»
            with self.assertRaises(KaskadError, msg=repr(bad)):
                model.reassign_ru_via(copy.deepcopy(st), HOME, bad)
        a = copy.deepcopy(st)
        self.assertEqual(model.reassign_ru_via(a, HOME, OFFICE), {"users": [u["id"]], "devices": [d["id"]]})
        self.assertEqual([o.get("ru_via") for o in a["gateway"]["users"] + a["gateway"]["devices"]],
                         [OFFICE, "direct", OFFICE, OFFICE])
        b = copy.deepcopy(st)
        model.reassign_ru_via(b, HOME, "direct")
        self.assertEqual([o.get("ru_via") for o in b["gateway"]["users"] + b["gateway"]["devices"]],
                         ["direct", "direct", "direct", OFFICE])


class OpsCase(RulesGwCase):
    def node(self, consent=False, profile=None, enabled=True):
        """The scenario of test_gw_net (Мама u-00000001: d1, d3; Дача u-00000002: d2) with the lists and the groups."""
        st = self.scenario(enabled=enabled)
        seed_small()
        with_groups(st)
        st["gateway"]["rules"] = {"default": "all_exit"}
        if profile:
            st["gateway"]["users"][0]["rules"] = profile
        if consent:
            consented(st)
        state.save(st)
        return state.load()

    def raw(self):
        return self.read("/etc/kaskad/state.json")


class OpsTest(OpsCase):
    def test_set_ru_via_through_change(self):
        self.node()
        n = len(self.w.runner.calls)
        res = rops.set_ru_via(self.w.runner, "user", "u-00000001", HOME)
        self.assertEqual((res["ok"], res["target"], res["id"], res["ru_via"], res["changed"]),
                         (True, "user", "u-00000001", HOME, True))
        self.assertIsNotNone(res["apply"])                  # the gateway is on: applied as every change of the model
        self.assertGreater(len(self.w.runner.calls), n)
        st = state.load()
        self.assertEqual(model.get_user(st, "u-00000001")["ru_via"], HOME)
        self.assertEqual(model.ru_via_of(st, model.get_device(st, "d-00000003")), {"via": HOME, "from": "user"})
        self.assertFalse(rops.set_ru_via(self.w.runner, "user", "u-00000001", HOME)["changed"])
        self.assertTrue(rops.set_ru_via(self.w.runner, "device", "d-00000001", "direct")["changed"])
        res = rops.set_ru_via(self.w.runner, "user", "u-00000001", "inherit")
        self.assertEqual((res["ru_via"], res["changed"]), ("inherit", True))
        self.assertNotIn("ru_via", model.get_user(state.load(), "u-00000001"))
        self.assertIn("RULES ru_via: user Мама (u-00000001) -> inherit", self.read("/var/log/kaskad.log"))

    def test_the_gateway_off_writes_without_apply(self):
        self.node(enabled=False)
        res = rops.set_ru_via(None, "device", "d-00000002", OFFICE)
        self.assertIsNone(res["apply"])
        self.assertEqual(model.get_device(state.load(), "d-00000002")["ru_via"], OFFICE)

    def test_refusals_write_nothing(self):
        self.node()
        before = self.raw()
        for args, code in ((("people", "u-00000001", HOME), errors.USAGE), (("user", "u-00000001", STREAM), errors.USAGE),
                           (("user", "u-00000001", "g-999999"), errors.USAGE), (("device", "d-0000000f", HOME), None)):
            with self.assertRaises(KaskadError, msg=args) as cm:
                rops.set_ru_via(self.w.runner, *args)
            if code is not None:
                self.assertEqual(cm.exception.code, code, args)
            else:
                self.assertIsInstance(cm.exception, model.NotFound)
        with self.assertRaises(KaskadError) as cm:
            rops.set_ru_via(self.w.runner, "people", "u-00000001", HOME)
        self.assertIn("user|device", str(cm.exception))
        self.assertEqual(self.raw(), before)

    def test_many_in_one_write_all_or_nothing(self):
        self.node()
        before = self.raw()
        with self.assertRaises(KaskadError):                # the second row is refused: the first is not written
            rops.set_ru_via_many(self.w.runner, [{"user": "u-00000001", "via": HOME},
                                                 {"user": "u-00000002", "via": STREAM}])
        self.assertEqual(self.raw(), before)
        for bad in ([], None, [{"via": HOME}], [{"user": "u-00000001"}], [{"user": "u-00000001", "device": "d-00000001",
                                                                           "via": HOME}], ["u-00000001"]):
            with self.assertRaises(KaskadError, msg=repr(bad)) as cm:
                rops.set_ru_via_many(self.w.runner, bad)
            self.assertEqual(cm.exception.code, errors.USAGE)
        self.assertEqual(self.raw(), before)
        res = rops.set_ru_via_many(self.w.runner, [{"user": "u-00000001", "via": HOME},
                                                   {"user": "u-00000002", "via": OFFICE},
                                                   {"device": "d-00000003", "via": "direct"},
                                                   {"device": "d-00000001", "via": "inherit"}])
        self.assertEqual(res["changed"], 3)
        self.assertEqual([r["ru_via"] for r in res["items"]], [HOME, OFFICE, "direct", "inherit"])
        st = state.load()
        self.assertEqual([model.ru_via_of(st, d)["via"] for d in st["gateway"]["devices"]], [HOME, OFFICE, "direct"])
        # REV-s1-q1 №3: the same person twice — changed once, by the value that stays written
        res = rops.set_ru_via_many(self.w.runner, [{"user": "u-00000002", "via": HOME},
                                                   {"user": "u-00000002", "via": "direct"}])
        self.assertEqual((res["changed"], [r["changed"] for r in res["items"]]), (1, [False, True]))
        res = rops.set_ru_via_many(self.w.runner, [{"user": "u-00000002", "via": HOME},
                                                   {"user": "u-00000002", "via": "direct"}])
        self.assertEqual(res["changed"], 0)                 # back to what it was: nothing changed


class ConsentTest(OpsCase):
    """§2.5: the consent ⚠РФ — ``ru_via`` neither changes nor needs it; ``rfgate.selected`` looks at the profile only."""

    def test_no_consent_is_asked_or_given(self):
        self.node()                                         # nobody on «Россия напрямую», no consent
        res = rops.set_ru_via(self.w.runner, "user", "u-00000001", HOME)
        st = state.load()
        self.assertTrue(res["changed"])
        self.assertFalse(rfgate.selected(st))
        self.assertNotIn("rf_consent", st["gateway"])
        self.assertEqual(rfgate.mode(st), "none")

    def test_the_consent_in_force_is_kept_as_it_is(self):
        self.node(consent=True, profile="ru_direct")
        was = state.load()["gateway"]["rf_consent"]
        rops.set_ru_via(self.w.runner, "user", "u-00000001", HOME)
        rops.set_ru_via(self.w.runner, "user", "u-00000001", "direct")
        self.assertEqual(state.load()["gateway"]["rf_consent"], was)

    def test_a_chosen_profile_without_consent_does_not_refuse_ru_via(self):
        """A node whose ``ru_direct`` is chosen and not applied (the mode ``need``, an import): ``ru_via`` brings no new
        «Россия напрямую», so the write is not refused and gives no consent; the profile still does not act."""
        st = self.node(profile="ru_direct")
        self.assertTrue(rfgate.selected(st))
        self.assertFalse(rfgate.allowed(st))
        rops.set_ru_via(self.w.runner, "user", "u-00000001", HOME)
        st = state.load()
        self.assertFalse(rfgate.allowed(st))
        self.assertNotIn("rf_consent", st["gateway"])
        row = [d for d in rops.status(st)["devices"] if d["id"] == "d-00000001"][0]
        self.assertEqual((row["ru"]["via"], row["ru_applies"]), (HOME, False))

    def test_a_tick_is_not_taken_by_ru_via(self):
        """``set_ru_via`` takes no ``rf``: there is no way to slip a consent in through it."""
        import inspect
        for fn in (rops.set_ru_via, rops.set_ru_via_many):
            self.assertNotIn("rf", inspect.signature(fn).parameters)


class StatusTest(OpsCase):
    """§2.5: «действует только вместе с «Россия напрямую»» — the choice is kept, ``ru_applies`` says whether it acts;
    §8.2: ``ru_groups`` for the line «Российские сайты»."""

    def rows(self, st):
        doc = rops.status(st)
        return doc, dict((d["id"], d) for d in doc["devices"]), dict((u["id"], u) for u in doc["users"])

    def test_kept_but_acts_only_with_russia_directly(self):
        self.node(consent=True, profile="ru_direct")
        rops.set_ru_via(self.w.runner, "user", "u-00000001", HOME)
        rops.set_ru_via(self.w.runner, "user", "u-00000002", OFFICE)
        st = state.load()
        doc, devs, users = self.rows(st)
        self.assertEqual((devs["d-00000001"]["ru_via"], devs["d-00000001"]["ru"]), ("inherit", {"via": HOME,
                                                                                               "from": "user"}))
        self.assertTrue(devs["d-00000001"]["ru_applies"])   # Мама: ru_direct with the consent and the lists
        self.assertTrue(devs["d-00000003"]["ru_applies"])
        self.assertFalse(devs["d-00000002"]["ru_applies"])  # Дача: all_exit — kept, does nothing
        self.assertEqual(devs["d-00000002"]["ru"]["via"], OFFICE)
        self.assertEqual((users["u-00000001"]["ru_via"], users["u-00000001"]["ru"]),
                         (HOME, {"via": HOME, "from": "user"}))
        self.assertEqual(doc["ru_groups"], [
            {"id": HOME, "name": "Дом", "label": "Дом", "users": ["u-00000001"],
             "devices": ["d-00000001", "d-00000003"], "applies": ["d-00000001", "d-00000003"]},
            {"id": OFFICE, "name": "Офис", "label": "Офис", "users": ["u-00000002"], "devices": ["d-00000002"],
             "applies": []}])
        # a route «block» by choice: no Russia at all, the choice is kept
        model.get_device(st, "d-00000001")["route"] = {"via": "block", "fail": "block"}
        state.save(st)
        st = state.load()
        _doc, devs, _users = self.rows(st)
        self.assertEqual((devs["d-00000001"]["ru"]["via"], devs["d-00000001"]["ru_applies"]), (HOME, False))
        # the consent gone (a new node): nothing acts, the choice stays
        st["gateway"].pop("rf_consent")
        state.save(st)
        st = state.load()
        doc, devs, _users = self.rows(st)
        self.assertFalse(any(d["ru_applies"] for d in devs.values()))
        self.assertEqual(devs["d-00000003"]["ru"]["via"], HOME)
        self.assertEqual(doc["ru_groups"][0]["applies"], [])

    def test_no_russian_group(self):
        st = self.node()
        st["gateway"].pop("exit_policy")
        state.save(st)
        doc, devs, users = self.rows(state.load())
        self.assertEqual(doc["ru_groups"], [])
        self.assertEqual(devs["d-00000001"]["ru"], {"via": "direct", "from": "default"})
        self.assertEqual(users["u-00000001"]["ru"], {"via": "direct", "from": "default"})

    def test_a_broken_reference_is_shown(self):
        st = self.node()
        model.get_user(st, "u-00000001")["ru_via"] = "g-999999"
        state.save(st)
        _doc, devs, users = self.rows(state.load())
        self.assertEqual(devs["d-00000001"]["ru"], {"via": "direct", "from": "user", "broken": "g-999999"})
        self.assertEqual(users["u-00000001"]["ru_via"], "g-999999")


class DeleteGroupTest(PolicyCase):
    """§2.5, §8.5: the deletion of a Russian group that people chose in ``ru_via`` is refused without an explicit
    ``reassign`` (409 ``need`` with ``data.refs``); ``reassign`` — ``direct`` or another Russian group, never a foreign
    one; the refs are rewritten in the same write."""

    def setUp(self):
        super().setUp()
        self.home = self.add_group("Дом", role="ru")
        self.office = self.add_group("Офис", role="ru")
        with state.transaction() as st:
            model.get_user(st, self.uid)["ru_via"] = self.home
            model.get_device(st, self.did)["ru_via"] = self.home
        snapshot.forget()

    def url(self, gid, **q):
        return "/api/gw/groups/%s?" % gid + "&".join("%s=%s" % kv for kv in sorted(dict(q, rev=self.rev()).items()))

    def test_refused_without_reassign(self):
        before = self.state_bytes()
        doc = self.err(409, "DELETE", self.url(self.home))
        self.assertEqual(doc["code"], "need")
        self.assertEqual(doc["data"]["refs"], {"users": [self.uid], "devices": [self.did]})
        self.assertEqual(self.state_bytes(), before)
        doc = self.err(400, "DELETE", self.url(self.home, reassign="main"))     # a foreign group is no ru_via
        self.assertEqual(doc["code"], "field")
        self.assertEqual(self.state_bytes(), before)
        dry = self.ok("DELETE", self.url(self.home, reassign="direct", dry=1))
        self.assertEqual(dry["refs"], {"users": [self.uid], "devices": [self.did]})
        self.assertEqual(self.state_bytes(), before)        # a dry run writes nothing

    def test_reassign_to_another_russian_group(self):
        self.ok("DELETE", self.url(self.home, reassign=self.office))
        st = state.load()
        self.assertIsNone(P.group(P.stored(st), self.home))
        self.assertEqual((model.get_user(st, self.uid)["ru_via"], model.get_device(st, self.did)["ru_via"]),
                         (self.office, self.office))

    def test_reassign_direct(self):
        with state.transaction() as st:                     # the person on «Офис», the device on «Дом»
            model.get_user(st, self.uid)["ru_via"] = self.office
        snapshot.forget()
        self.ok("DELETE", self.url(self.home, reassign="direct"))
        st = state.load()
        d = model.get_device(st, self.did)
        self.assertEqual(d["ru_via"], "direct")             # explicit: not «inherit» -> the person's «Офис»
        self.assertEqual(model.ru_via_of(st, d), {"via": "direct", "from": "device"})
        self.assertEqual(model.get_user(st, self.uid)["ru_via"], self.office)

    def test_a_group_nobody_chose_goes_without_reassign(self):
        self.ok("DELETE", self.url(self.office))
        self.assertIsNone(P.group(P.stored(state.load()), self.office))

    def test_a_foreign_group_is_as_before(self):
        """The routes of a foreign group: ``reassign`` a foreign group or ``direct``, ``ru_via`` untouched."""
        gid = self.add_group("Стриминг")
        with state.transaction() as st:
            model.get_device(st, self.did)["route"] = {"via": "group:" + gid, "fail": "block"}
        snapshot.forget()
        self.assertEqual(self.err(400, "DELETE", self.url(gid, reassign=self.office))["code"], "field")
        with state.transaction() as st:                     # REV-s1-q1 №5 (M12): a hand edit — ru_via of a FOREIGN group
            model.get_user(st, self.uid)["ru_via"] = gid
        snapshot.forget()
        st = state.load()
        self.assertEqual(model.ru_via_refs(st, gid), {"users": [], "devices": []})
        self.ok("DELETE", self.url(gid, reassign="main"))
        st = state.load()
        d = model.get_device(st, self.did)
        self.assertEqual((d["route"]["via"], d["ru_via"]), ("pool", self.home))


class InvariantWithRuVia(inv.InvariantTest):
    """D-136 with ``ru_via`` and the Russian legs (Q1 + Q4): the random states of ``test_ru_invariant`` plus Russian
    groups holding REAL exits of upstream-ru.txt (an AmneziaWG leg kawg201 and a VLESS key, numbers 201+, tags «gr-»)
    and random ``ru_via`` of people and devices (a group, ``direct``, ``inherit``, a deleted group, garbage).

    Two oracles (REV-s1-q1 №1 — not the byte equality alone):
    * the R2 oracle of ``test_ru_invariant`` (``violations``) on what the node would draw with both files read;
    * the oracle of the Russian legs (``ru_violations``): the observatory, ``gw-all``, ``gw-vless`` and every balancer
      never select «gr-»; ``gw_route`` and the cells of numgen never carry a mark of a Russian leg or exit (457–488,
      0x3c9–0x3e8: the traffic of a group never goes to a home router); a rule to «gr-<id>» only for a user whose
      ``ru_via`` resolves to the group of that exit, with the permission ⚠РФ and the profile «Россия напрямую» (none
      before Q2).
    Q2 (§2.8): the third oracle — that of ``ru_target`` (``rt_violations``), written from the table of §2.8, not from
    the code: gw_rd only for ``ru_via`` «direct»; gw_rx only for a Russian group, its mark — an exit of THAT group, or
    its last step (0x201 only when the group falls «напрямую», else 0x2ff); gw_rd and gw_rx never share an address; a
    rule «напрямую» of the lists never names a user whose Russian group does not fall «напрямую»; without ``ru_via``
    there is no gw_rx at all (was: the byte equality of Q1). The coverage is honest: at least ``MIN_COVERED`` states have
    the permission and a device with «Россия напрямую», active, whose ``ru_via`` is a Russian group (REV-s1-q1 №1: 5 of
    120 before), ``MIN_COVERED`` of them with that device in gw_rx on an exit of its group, and the Russian outbounds
    are drawn in most of them."""

    N = 120
    MIN_COVERED = 10

    locals().update((name, None) for name in dir(inv.InvariantTest) if name.startswith("test"))   # its own run there

    def setUp(self):
        super().setUp()
        from tests.fakes_gw import awg_line, write_ru_pool
        write_ru_pool([awg_line(31, name="Роутер", endpoint="203.0.113.31:51820", address="10.8.31.2/32"),
                       "vless://aaaaaaaa-1111-2222-3333-444444444444@203.0.113.32:443?type=tcp&security=reality"
                       "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome"
                       "#Dacha"])
        self.ru_rows = exits.pool(self.w.runner, roles=("ru",))
        self.assertEqual([e["index"] for e in self.ru_rows], [201, 202])
        self.ru_ids = [e["id"] for e in self.ru_rows]
        snap = exits.load_snapshot()                          # Q2: the Russian exits fit (drawn(): alive=set() — down)
        for i in self.ru_ids:
            snap.setdefault("exits", {})[i] = {"alive": True, "state": "ok", "reasons": []}
        exits.save_snapshot(snap)
        self.ru_marks = set(e["mark"] for e in self.ru_rows) | set(range(457, 489)) | set(range(0x3c9, 0x3e9))

    def with_ru_via(self, st, rnd):
        st = copy.deepcopy(st)
        g = st["gateway"]
        doc = g.get("exit_policy")
        if not isinstance(doc, dict):
            doc = g["exit_policy"] = P.default_doc("compat")
        home = P.default_group(HOME, "ru", "ru_home", name="Дом")
        home["tiers"][0]["exits"] = list(self.ru_ids)          # the Russian exits are in their group
        doc["groups"].append(home)
        if rnd.random() < 0.5:
            doc["groups"].append(P.default_group(OFFICE, "ru", "ru_home", name="Офис"))
        for o in g["users"] + g["devices"]:
            v = rnd.choice((None, "direct", "inherit", HOME, HOME, HOME, OFFICE, "g-999999", inv.GID, "main", 7))
            if v is not None:
                o["ru_via"] = v
        if rnd.random() < 0.4:                                   # the coverage (REV-s1-q1 №1): permission + profile
            g["rf_consent"] = inv.good_consent()
            d = rnd.choice(g["devices"])
            d["rules"], d["enabled"], d["ru_via"] = inv.RU, True, HOME
        return st

    def drawn_all(self, st, rnd):
        """``drawn`` with the pool of BOTH files (the Russian rows too) and the xray config it rendered."""
        box = {}
        real = xray.render

        def cap(*a, **kw):
            box["cfg"] = real(*a, **kw)
            return box["cfg"]
        keep = self.pool
        self.pool = keep + self.ru_rows
        try:
            with mock.patch.object(xray, "render", cap):
                got = self.drawn(st, rnd)
        finally:
            self.pool = keep
        return got, box.get("cfg") or {}

    def ru_violations(self, st, got, cfg):
        bad = []
        for b in ((cfg.get("routing") or {}).get("balancers") or []):
            if any(str(x).startswith("gr-") for x in b.get("selector") or []):
                bad.append("balancer %s selects gr-" % b.get("tag"))
        # T-q круг 3 (З-2, решение координатора): the observatory — the foreign exits only, never «gr-» (a Russian
        # xray exit has its own probe through probe-<id>)
        for obs in ("observatory", "burstObservatory"):
            if any(str(x).startswith("gr-") for x in (cfg.get(obs) or {}).get("subjectSelector") or []):
                bad.append("%s selects gr-" % obs)
        for c in got.get("numgen", ()):
            if c in self.ru_marks:
                bad.append("numgen cell %s: a Russian leg" % hex(c))
        for ip, m in sorted((got.get("elems") or {}).items()):
            if m in self.ru_marks:
                bad.append("gw_route %s: a Russian mark %s" % (ip, hex(m)))
        by_login = inv.oracle_users(st["gateway"]["devices"])  # «<login>.h2» of Hysteria2 too (3.3.7.1 Д-1)
        homes = dict((e["tag"], g.get("id")) for g in (st["gateway"].get("exit_policy") or {}).get("groups") or []
                     for t in g.get("tiers") or [] for e in self.ru_rows if e["id"] in (t.get("exits") or []))
        own = dict((e["tag"], {exits.tproxy_tag(e["id"]), xray.probe_tag(e["id"])}) for e in self.ru_rows)
        for r in (cfg.get("routing") or {}).get("rules") or []:
            tag = str(r.get("outboundTag") or "")
            if not tag.startswith("gr-"):
                continue
            ins = set(r.get("inboundTag") or [])
            if ins and ins <= own.get(tag, set()) and not r.get("user"):
                continue                    # its own TPROXY / probe inbound (px-<id>): reached by its mark or the probe
            for user in r.get("user") or [None]:
                d = by_login.get(user)
                ok = (d is not None and inv.oracle_allowed(st) and inv.oracle_profile(st, d) == inv.RU
                      and inv.oracle_active(st, d) and model.ru_via_of(st, d)["via"] == homes.get(tag))
                if not ok:
                    bad.append("xray %s -> %s without ⚠РФ, the profile or its ru_via" % (user, tag))
        return bad

    def rt_violations(self, st, got):
        bad = []
        groups = dict((g.get("id"), g) for g in (st["gateway"].get("exit_policy") or {}).get("groups") or []
                      if isinstance(g, dict) and g.get("role") == "ru")
        home_of = dict((ex, gid) for gid, g in groups.items() for t in g.get("tiers") or [] for ex in t.get("exits") or [])
        exit_of = dict((e["mark"], e["id"]) for e in self.ru_rows)
        devs = st["gateway"]["devices"]
        ips = dict((ip, d) for d in devs for ip in (d.get("ips") or {}).values())
        rx = got.get("rx") or {}
        for ip in sorted(set(got["rd"]) & set(rx)):
            bad.append("%s in gw_rd and gw_rx" % ip)
        for ip in got["rd"]:
            d = ips.get(ip)
            if d is not None and model.ru_via_of(st, d)["via"] != "direct":
                bad.append("gw_rd %s: ru_via %s" % (ip, model.ru_via_of(st, d)["via"]))
        for ip, m in sorted(rx.items()):
            d = ips.get(ip)
            via = model.ru_via_of(st, d)["via"] if d is not None else None
            if via not in groups:
                bad.append("gw_rx %s: ru_via %s" % (ip, via))
            elif m in exit_of:
                if home_of.get(exit_of[m]) != via:
                    bad.append("gw_rx %s: the exit %s of another group than %s" % (ip, exit_of[m], via))
            elif m == exits.MARK_DIRECT:
                if groups[via].get("fail") != "direct":
                    bad.append("gw_rx %s: 0x201, the group %s falls to block" % (ip, via))
            elif m != exits.MARK_BLOCK:
                bad.append("gw_rx %s: mark %s" % (ip, hex(m)))
        login = inv.oracle_users(devs)                  # «<login>.h2» of Hysteria2 too
        for tag, user in sorted(got["direct"], key=str):
            d = login.get(user)
            if d is None:                               # REV-q-int П-5: a user the oracle does not know — never skipped
                bad.append("xray %s: a direct rule of no device (%s)" % (user, tag))
                continue
            if inv.oracle_via(st, d) == "direct":
                continue
            via = model.ru_via_of(st, d)["via"]
            if via in groups and groups[via].get("fail") != "direct":
                bad.append("xray %s: «напрямую» of the lists, its Russian group %s" % (user, via))
        return bad

    def covered(self, st):
        return inv.oracle_allowed(st) and any(
            inv.oracle_profile(st, d) == inv.RU and inv.oracle_active(st, d)
            and model.ru_via_of(st, d)["via"] in (HOME, OFFICE) for d in st["gateway"]["devices"])

    def test_no_direct_russia_without_the_permission_and_nothing_changes(self):
        rnd = random.Random(inv.SEED + 1)
        covered = drawn_gr = on_exit = 0
        for i in range(self.N):
            st = self.random_state(rnd)
            st2 = self.with_ru_via(st, rnd)
            seed = rnd.random()
            got, cfg = self.drawn_all(st2, random.Random(seed))
            self.assertEqual(self.violations(st2, got), [], "state %d" % i)
            self.assertEqual(self.ru_violations(st2, got, cfg), [], "state %d" % i)
            self.assertEqual(self.rt_violations(st2, got), [], "state %d" % i)
            st1 = copy.deepcopy(st2)
            for o in st1["gateway"]["users"] + st1["gateway"]["devices"]:
                o.pop("ru_via", None)
            got1, _cfg1 = self.drawn_all(st1, random.Random(seed))
            self.assertEqual(got1["rx"], {}, "state %d" % i)                # nobody through a group: no gw_rx entry
            self.assertEqual(self.violations(st1, got1), [], "state %d" % i)
            covered += self.covered(st2)
            on_exit += any(m not in (exits.MARK_DIRECT, exits.MARK_BLOCK) for m in got["rx"].values())
            drawn_gr += any(str(o.get("tag")).startswith("gr-") for o in cfg.get("outbounds") or [])
        self.assertGreaterEqual(covered, self.MIN_COVERED)
        self.assertGreaterEqual(on_exit, self.MIN_COVERED)     # gw_rx on a Russian exit — really checked
        self.assertGreater(drawn_gr, self.N // 2)              # the Russian legs are really in the config

    def test_the_rt_oracle_never_skips_an_unknown_user(self):
        """REV-q-int П-5: a direct rule of a user the oracle does not know (and one without a user) is a violation of
        ``rt_violations`` itself — never skipped, as «<login>.h2» once was."""
        rnd = random.Random(inv.SEED + 3)
        st = self.with_ru_via(self.random_state(rnd), rnd)
        got = {"rd": [], "rx": {}, "direct": {("socks-in", "ghost-9999"), ("vless-in", None)}}
        bad = self.rt_violations(st, got)
        self.assertEqual(len(bad), 2, bad)
        self.assertTrue(all("of no device" in b for b in bad), bad)

    def test_a_planted_route_to_a_russian_leg_is_caught(self):
        """The second oracle is not empty: a balancer over «gr-», a Russian mark in gw_route and a rule to «gr-» of
        a user without ⚠РФ are each found."""
        rnd = random.Random(inv.SEED + 2)
        st = self.with_ru_via(self.random_state(rnd), rnd)
        st["gateway"].pop("rf_consent", None)
        got, cfg = self.drawn_all(st, random.Random(1))
        self.assertEqual(self.ru_violations(st, got, cfg), [])
        tag = self.ru_rows[0]["tag"]
        user = inv.oracle_login(st["gateway"]["devices"][0])
        cfg = copy.deepcopy(cfg)
        cfg.setdefault("routing", {}).setdefault("balancers", []).append({"tag": "x", "selector": ["gr-"]})
        cfg["routing"].setdefault("rules", []).append({"type": "field", "user": [user], "outboundTag": tag})
        got = dict(got, elems=dict(got.get("elems") or {}, **{"10.77.1.99": self.ru_rows[0]["mark"]}))
        bad = self.ru_violations(st, got, cfg)
        self.assertEqual(len(bad), 3, bad)


if __name__ == "__main__":
    unittest.main()
