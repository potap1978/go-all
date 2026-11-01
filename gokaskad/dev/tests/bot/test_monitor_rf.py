"""R7 — the bot's ONE message about ⚠РФ (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.3, §6): on entering ``legacy``
(after the update), ``need`` or when a new organisation appears in ``uncovered``; no repeats (``rf_sent`` in
monitor.json, written after Telegram took it; a blink of the lists says nothing again), no buttons of confirmation — only the panel sign-in; nothing on a node
without the gateway and on a new node (``all_exit``)."""

import os

from kaskad import state
from kaskad.rules import ops as rops
from kaskad.rules import rfgate
from tests.bot.fake_tg import OWNER
from tests.bot.test_monitor import IP, MonitorCase
from tests.test_rules_rf_consent import RfCase

CONSENT = {"confirm": True, "via": "panel", "who": "admin", "lang": "ru"}


class RfBotTest(MonitorCase):
    def setUp(self):
        super(RfBotTest, self).setUp()
        os.unlink(self.path("/etc/kaskad/monitors/%s.conf" % IP))
        self.mon.forget()

    def node(self, gen, default=None, consent=False):
        st = state.load()
        st["gen"] = gen
        g = st.setdefault("gateway", {})
        g["inbounds"] = {"wg": {"port": 51820}}                       # the gateway is set up
        if default:
            g["rules"] = {"default": default}
        g.pop("rf_consent", None)
        if consent:
            g["rf_consent"] = {"at": "2026-10-18T00:00:00Z", "via": "panel", "who": "admin", "orgs": ["meta"],
                               "registry": "x", "lang": "ru"}
        state.save(st)

    def rf_texts(self):
        return [c for c in self.tg.sent("sendMessage", chat=OWNER) if "Россия напрямую" in c.text]

    def test_legacy_once_with_the_panel_button_only(self):
        self.node(1)
        self.assertEqual(rfgate.mode(state.load()), "legacy")
        for _ in range(3):
            self.tick()
        (c,) = self.rf_texts()
        self.assertIn("работает как раньше", c.text)
        self.assertIn("17 → 8", c.text)
        self.assertEqual([d for _, d in c.buttons()], ["gwl"])          # the panel sign-in; no «Подтвердить»
        self.assertEqual(self.stored()["rf_sent"], "legacy")
        # a new process of the bot: the mark is kept — no repeat
        from kaskad.bot.monitor import Monitor
        self.app.monitor = self.mon = Monitor(self.app, sync=True)
        self.tick()
        self.assertEqual(len(self.rf_texts()), 1)

    def test_not_taken_is_said_again(self):
        self.node(1)
        self.tg.fail("sendMessage", 502, "Bad Gateway")
        self.tick()
        self.assertIsNone(self.stored().get("rf_sent"))
        self.tick()
        self.assertEqual(self.stored()["rf_sent"], "legacy")

    def test_need_then_full_then_need_again(self):
        self.node(2, default="ru_direct")                             # a hand edit on a new node: chosen, not on
        self.tick()
        self.assertEqual(len(self.rf_texts()), 1)
        self.assertIn("не включена", self.rf_texts()[0].text)
        self.node(2, default="ru_direct", consent=True)               # confirmed — calm
        self.tick()
        self.assertEqual(self.stored()["rf_sent"], "full")
        self.node(2, default="ru_direct")                             # the consent gone again — news again
        self.tick()
        self.assertEqual(len(self.rf_texts()), 2)

    def test_new_node_and_no_gateway_say_nothing(self):
        self.node(2)
        self.tick()
        st = state.load()
        st["gateway"].pop("inbounds")
        st["gen"] = 1
        state.save(st)
        self.tick()
        self.assertEqual(self.rf_texts(), [])

    def test_uncovered_by_its_organisations(self):
        self.node(2)
        RfCase.scan_with(self, ["meta"])
        rops.set_default(self.app.runner, "ru_direct", rf=dict(CONSENT, orgs=["meta"]))
        self.tick()
        self.assertEqual(self.rf_texts(), [])
        RfCase.scan_with(self, ["meta", "fbk"])
        self.tick()
        self.tick()
        (c,) = self.rf_texts()
        self.assertIn("появилась организация ⚠РФ", c.text)
        self.assertEqual(self.stored()["rf_sent"], "uncovered:fbk")

    def test_a_blink_of_the_lists_is_not_said_again(self):
        """REV-s1-r67 П-4: the organisation leaves the list and comes back (or the mode calms down between) — no repeat;
        only an organisation never named is news."""
        self.node(2)
        RfCase.scan_with(self, ["meta"])
        rops.set_default(self.app.runner, "ru_direct", rf=dict(CONSENT, orgs=["meta"]))
        RfCase.scan_with(self, ["meta", "fbk"])
        self.tick()
        self.assertEqual(len(self.rf_texts()), 1)
        RfCase.scan_with(self, ["meta"])                     # fbk gone from the lists: full, calm
        self.tick()
        self.assertEqual(self.stored()["rf_sent"], "uncovered:fbk")
        RfCase.scan_with(self, ["meta", "fbk"])              # back again
        self.tick()
        self.tick()
        self.assertEqual(len(self.rf_texts()), 1)
        RfCase.scan_with(self, ["meta", "fbk", "zorg"])      # a really new one — said, by itself
        self.tick()
        self.assertEqual(len(self.rf_texts()), 2)
        self.assertIn("(zorg)", self.rf_texts()[1].text)
        self.assertEqual(self.stored()["rf_sent"], "uncovered:fbk,zorg")
        st = state.load()                                    # the consent gone (all_exit) — a new story
        st["gateway"]["rules"] = {"default": "all_exit"}
        st["gateway"].pop("rf_consent", None)
        state.save(st)
        self.tick()
        self.assertEqual(self.stored()["rf_sent"], "none")
