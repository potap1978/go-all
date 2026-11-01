"""R7 — the step «rules» of the wizard and its item «Россия напрямую» (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.3,
§9, D-019): a new node stays «всё через выход»; ``ru_direct: true`` without the tick ⚠РФ — ``need_input`` with the
block, the profile does not change; with ``confirm_not_rf: true`` and ``orgs`` of the block shown — the consent
``via = setup`` on them only (SEC-s1-r67 №1). The page: the tick of its own, off by default, «Сохранить» inactive
without it, the shown organisations sent; on an updated node (legacy) or with a new organisation — «Подтвердить», not
«Включить» (REV-s1-r67 П-2); texts RU and EN."""

import os
import re

from kaskad import state
from kaskad.gw import model as gmodel
from kaskad.setup import steps
from tests.helpers import RootCase
from tests.setup import test_setup_core as core

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "lib", "kaskad", "panel", "static", "setup")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class Base(RootCase):
    """The stubs of StepsTest (panel, link, runner) without its tests."""
    setUp, tearDown, password = core.StepsTest.setUp, core.StepsTest.tearDown, core.StepsTest.password


class RulesStepTest(Base):
    def setUp(self):
        super().setUp()
        self.password()

    def test_read_only_shows_all_exit_and_the_block(self):
        out = steps.run("rules", {}, self.runner)
        self.assertEqual((out["state"], out["text"]), ("ok", ""))
        d = out["data"]
        self.assertEqual(d["profile"], "all_exit")
        self.assertEqual((d["rf"]["required"], d["rf"]["mode"], d["rf"]["uncovered"]), (True, "none", []))
        self.assertEqual(d["rf"]["orgs"], ["meta", "fbk"])                 # what the page sends back as shown
        self.assertIn("признана экстремистской на территории Российской Федерации", d["rf"]["block"])
        self.assertEqual(d["rf"]["consent"], "Скрипт не используется на территории РФ")

    def test_without_the_tick_need_and_nothing_changes(self):
        before = state.load()
        for body in ({"ru_direct": True}, {"ru_direct": True, "confirm_not_rf": "yes"}, {"ru_direct": True, "confirm_not_rf": 1},
                     {"ru_direct": True, "confirm_not_rf": True},                       # the shown block not said
                     {"ru_direct": True, "confirm_not_rf": True, "orgs": ["meta"]},     # narrower than the block now
                     {"ru_direct": True, "confirm_not_rf": True, "orgs": "meta,fbk"}):  # not a list
            out = steps.run("rules", body, self.runner)
            self.assertEqual(out["state"], "need_input", body)
            self.assertEqual((out["error"]["code"], out["error"]["field"]), ("rf_consent", "confirm_not_rf"))
            self.assertEqual(out["data"]["need"]["need"], "rf_consent")
            self.assertIn("Скрипт не используется на территории РФ", out["text"])
        st = state.load()
        self.assertEqual(gmodel.rules_conf(st)["default"], "all_exit")
        self.assertNotIn("rf_consent", st.get("gateway") or {})
        self.assertEqual(gmodel.rules_conf(before), gmodel.rules_conf(st))

    def test_with_the_tick_consent_via_setup_and_off_resets_it(self):
        out = steps.run("rules", {"ru_direct": True, "confirm_not_rf": True, "orgs": ["meta", "fbk"]}, self.runner)
        self.assertEqual(out["state"], "ok", out)
        self.assertEqual(state.load()["gateway"]["rf_consent"]["orgs"], ["meta", "fbk"])
        self.assertEqual((out["data"]["profile"], out["data"]["rf"]["mode"]), ("ru_direct", "full"))
        c = state.load()["gateway"]["rf_consent"]
        self.assertEqual((c["via"], c["who"]), ("setup", None))
        out = steps.run("rules", {"ru_direct": False}, self.runner)
        self.assertEqual(out["data"]["profile"], "all_exit")
        self.assertNotIn("rf_consent", state.load()["gateway"])     # all_exit everywhere — the consent is reset (§3.4)

    def test_a_string_is_not_a_boolean(self):
        """«было до правки» (REV-s1-r67): ``bool("false")`` turned «Россия напрямую» on — now only a JSON boolean."""
        for v in ("false", "true", 1, None):
            out = steps.run("rules", {"ru_direct": v, "confirm_not_rf": True, "orgs": ["meta", "fbk"]}, self.runner)
            self.assertEqual((out["state"], out["text"]), ("ok", ""), v)
            self.assertEqual(out["data"]["profile"], "all_exit", v)
        self.assertNotIn("rf_consent", state.load().get("gateway") or {})

    def test_english_block(self):
        from kaskad.i18n import use_lang
        with use_lang("en"):
            out = steps.run("rules", {"ru_direct": True}, self.runner)
        self.assertEqual(out["state"], "need_input")
        self.assertIn("The script is not used in the Russian Federation", out["text"])
        self.assertIsNone(re.search("[А-Яа-яЁё]", out["data"]["rf"]["block"].replace("ФБК", "")))


class RulesPageTest(Base):
    def test_the_item_has_its_tick_off_by_default(self):
        js = read("setup.js")
        body = js[js.index("function rulesItem()"):js.index("VIEW.extras = () =>")]
        self.assertIn('step("rules", { ru_direct: true, confirm_not_rf: u.tick === true, orgs: arr(rf.orgs) })', body)
        self.assertIn('u.tick = false', body[body.index('"need_input"'):])   # a new block — the tick again
        self.assertIn('rf.mode === "legacy"', body)                  # legacy: «Подтвердить», not «Включить»
        self.assertIn("arr(rf.uncovered).length", body)               # a new organisation — confirmable here too
        self.assertIn("disabled: !u.tick", body)                 # «Сохранить» inactive without the tick
        self.assertIn("if (!u.want) u.tick = false", body)       # turning the wish off drops the tick
        self.assertIn("rf.block", body)
        self.assertIn("rf.consent", body)                         # the words of the tick — the core's, not the page's
        self.assertIn('step("rules", {})', js)
        for lang in ("ru", "en"):
            i18n = read("setup-i18n-%s.js" % lang)
            for k in ("x.ruTitle", "x.ruDesc", "x.ruOff", "x.ruNeed", "x.ruWant", "x.ruWantD", "x.ruSave",
                      "x.ruOnNow", "x.ruTurnOff", "x.ruNew", "x.ruLegacyD", "x.ruNeedD", "x.ruNewD", "x.ruConfirm"):
                self.assertIn('"%s":' % k, i18n, (lang, k))
        en = read("setup-i18n-en.js")
        for k in re.findall(r'"(x\.ru\w+)": "([^"]*)"', en):
            self.assertIsNone(re.search("[А-Яа-яЁё]", k[1]), k)
