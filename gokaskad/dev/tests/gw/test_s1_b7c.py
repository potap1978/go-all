"""B7, круг 3 (REV-s1-b7-2, REV-s1-b7-sec-2): the event ``ru_guard`` once, not every tick (№1); a second opening after
the choice came back is told again, and that closing has words of its own (№2); the last line knows a direct rule of
any matcher, not the lists only (-sec-2 Н-1); the hook's gw_ruonly passes the last line (-sec-2 П-1); the file that runs
is the closed one when no mark could be written (придирка 1), and the closed one counts only what the state does not
give (придирка 2). The probes of the reviewers (scratchpad rev-b7-2, rev-b7-sec-2) are here as tests."""

import json
import os
from unittest import mock

from kaskad import state
from kaskad.errors import KaskadError
from kaskad.gw import collect, exits, live, net, ops, xray
from kaskad.gw import sys as gsys
from kaskad.i18n import tr
from kaskad.rules import apply as rapply
from tests.gw.test_s1_b5 import CONF, REFUSED, direct_users
from tests.gw.test_s1_b7 import UNIT
from tests.gw.test_s1_b7r2 import CLOSED, R2Case, ruguard

PLAIN = ["domain:ru", "domain:xn--p1ai"]


class GuardOnceTest(R2Case):
    def planted(self):
        """A render that draws the direct rules of the consent although the consent is gone (a bug of the code)."""
        self.socks_world()
        rules = json.loads(self.read(CONF))["routing"]["rules"]
        self.without_consent()
        real = xray.render

        def render(*a, **kw):
            cfg = real(*a, **kw)
            cfg["routing"]["rules"] = rules
            return cfg
        return mock.patch.object(xray, "render", side_effect=render)

    def test_xray_trip_is_told_once_over_ticks(self):
        # REV-s1-b7-2 №1 (P1): the render is drawn again every tick and the line trips every time — one event
        with self.planted():
            for _ in range(4):
                collect.tick(self.w.runner)
        self.assertEqual(self.kinds("ru_guard"), ["ru_guard"])
        self.assertEqual(self.conf_direct() or [], [])

    def test_nft_trip_is_told_once_over_ticks_and_checks(self):
        # P2: rules.apply.rd gives addresses without the consent — every plan and every check trips
        self.without_consent()
        ips = sorted(ip for d in state.load()["gateway"]["devices"] for ip in (d.get("ips") or {}).values())
        with mock.patch.object(rapply, "rd", lambda st, *a, **kw: list(ips)):
            for _ in range(3):
                collect.tick(self.w.runner)
                ops.check(state.load(), self.w.runner, repair=True)
        self.assertEqual(self.kinds("ru_guard"), ["ru_guard"])
        self.assertEqual(self.w.nft.elements("gw_rd"), [])

    def test_the_same_trip_again_after_an_hour_and_another_at_once(self):
        now = [1_000_000.0]
        with mock.patch.object(ruguard.time, "time", lambda: now[0]):
            def step(dt, where, n):
                now[0] += dt
                ruguard.trip(where, n)
                got = ruguard.tripped()
                if got is not None:
                    ruguard.told(got)
                return got and (got["where"], got["n"])
            self.assertEqual(step(0, "nft", 3), ("nft", 3))
            self.assertIsNone(step(600, "nft", 3))                            # the same within the hour: told
            self.assertEqual(step(1, "xray", 2), ("xray", 2))                 # another point: at once
            self.assertEqual(step(1, "nft", 4), ("nft", 4))                   # another number: at once
            self.assertIsNone(step(1, "nft", 4))
            self.assertEqual(step(3600, "nft", 4), ("nft", 4))                # an hour later: again

    def test_both_points_of_one_tick_are_told(self):
        ruguard.trip("nft", 1)
        ruguard.trip("xray", 2)
        events = []
        collect._ru_watch({}, events, lambda *_: None, self.w.runner)
        self.assertEqual(sorted(e["where"] for e in events if e["kind"] == "ru_guard"), ["nft", "xray"])


class OpenAgainTest(R2Case):
    def test_closed_by_the_consent_then_open_again_is_told(self):
        # REV-s1-b7-2 №2 (rev72d): xray cannot be stopped, its config refused; the consent goes, comes back, goes again
        cons = self.socks_world()["gateway"]["rf_consent"]
        self.w.xray.fail = REFUSED
        self.unstoppable()

        def consent(on):
            with state.transaction() as st:
                if on:
                    st["gateway"]["rf_consent"] = cons
                else:
                    st["gateway"].pop("rf_consent")
            collect.tick(self.w.runner)
            collect.tick(self.w.runner)
        consent(False)
        self.assertEqual(self.kinds(), ["xray_ru_open"])
        consent(True)
        self.assertEqual(self.kinds(), ["xray_ru_open", "xray_ru_closed"])
        closed = [e for e in self.events() if e["kind"] == "xray_ru_closed"][-1]
        self.assertEqual((closed.get("args") or {}).get("why"), "allowed")
        for lang in ("ru", "en"):                                             # not «restarted or stopped»
            self.assertEqual(live.event_text(closed, lang), tr("gw_live.event.xray_ru_closed_allowed", lang))
            self.assertNotEqual(live.event_text(closed, lang), tr("gw_live.event.xray_ru_closed", lang))
        self.assertEqual(xray.ru_told(), 0)
        consent(False)
        self.assertEqual(self.kinds(), ["xray_ru_open", "xray_ru_closed", "xray_ru_open"])
        self.assertIn("gw_xray_ru_open", [p["kind"] for p in ops.check(state.load(), self.w.runner)])

    def test_a_restarted_collector_after_the_consent_came_back_tells_the_next_opening(self):
        cons = self.socks_world()["gateway"]["rf_consent"]
        self.w.xray.fail = REFUSED
        self.unstoppable()
        with state.transaction() as st:
            st["gateway"].pop("rf_consent")
        collect.tick(self.w.runner)
        with state.transaction() as st:
            st["gateway"]["rf_consent"] = cons
        self.raises(state.load())                                             # gw apply narrows, not the collector
        collect.MEM.ru_open = 0                                               # a new collector process
        with state.transaction() as st:
            st["gateway"].pop("rf_consent")
        collect.tick(self.w.runner)
        self.assertEqual(self.kinds().count("xray_ru_open"), 2)


class ClosedFileTest(R2Case):
    def test_no_mark_anywhere_the_running_file_is_the_closed_one(self):
        # придирка 1 (rev72c): neither /run nor /etc takes the mark, the restart fails, xray does not stop — the file
        # that runs (with the direct rules) is kept as xray.closed.json, not the new one
        new, sec = self.without_consent()
        self.unstoppable()
        failing = mock.patch.object(gsys, "ensure_unit", side_effect=KaskadError("daemon-reload failed"))
        with self.run_full(also_etc=True), mock.patch.object(xray, "hot_apply", return_value=False), failing:
            data = self.raises(new)
        self.assertGreater(data.get("ru_open", 0), 0)
        self.assertTrue(direct_users(json.loads(self.read(CLOSED))))
        events = []
        collect._ru_watch(data, events, lambda *_: None, self.w.runner)
        self.assertEqual([e["kind"] for e in events], ["xray_ru_open"])
        with self.run_full(also_etc=True), failing:                           # the next call: still open, no 🔒
            try:
                data = self.apply(new)
            except KaskadError as e:
                data = e.data or {}
        self.assertGreater(xray.ru_open(self.w.runner, st=new), 0)
        self.assertTrue(direct_users(json.loads(self.read(CLOSED))))
        events = []
        collect._ru_watch(data, events, lambda *_: None, self.w.runner, (new, exits.pool(self.w.runner)))
        self.assertNotIn("xray_ru_closed", [e["kind"] for e in events])
        self.assertIn("gw_xray_ru_open", [p["kind"] for p in ops.check(state.load(), self.w.runner)])

    def test_the_closed_file_counts_only_what_the_state_does_not_give(self):
        # придирка 2 (P4): the consent stays, one device goes to all_exit — one path open, not both
        st = self.socks_world()
        self.w.xray.fail = REFUSED
        self.unstoppable()
        with state.transaction() as s:
            for d in s["gateway"]["devices"]:
                if d["id"] == st["gateway"]["devices"][0]["id"]:
                    d["rules"] = "all_exit"
        with self.run_full(also_etc=True):
            data = self.raises(state.load())
        self.assertEqual(data.get("ru_open"), 1)
        self.assertTrue(os.path.exists(self.path(CLOSED)))
        with self.run_full(also_etc=True):
            collect.tick(self.w.runner)
        self.assertEqual([(e["kind"], (e.get("args") or {}).get("n")) for e in self.events()
                          if e["kind"].startswith("xray_ru_")], [("xray_ru_open", 1)])
        self.assertEqual(xray.ru_open(self.w.runner, st=state.load()), 1)


class AnyMatcherTest(R2Case):
    def test_a_plain_domain_rule_without_the_consent_is_not_applied(self):
        # -sec-2 Н-1 (L8): Russia directly by ``domain:ru`` past rules.apply — not the lists, still the last line
        new, sec = self.without_consent()
        real = rapply.xray_rules
        users = sorted(rapply._user(d) for d in new["gateway"]["devices"])

        def emit(st, *a, **kw):
            return real(st, *a, **kw) + [{"type": "field", "inboundTag": ["socks-in"], "user": users,
                                          "domain": list(PLAIN), "outboundTag": "direct"}]
        with mock.patch.object(rapply, "xray_rules", emit):
            data = self.raises(new)
        self.assertGreater(data.get("ru_guard", 0), 0)
        self.assertEqual(ruguard.tripped()["where"], "xray")
        cfg = json.loads(self.read(CONF)) if os.path.exists(self.path(CONF)) else {}
        self.assertFalse([r for r in (cfg.get("routing") or {}).get("rules") or []
                          if "domain:ru" in (r.get("domain") or [])])

    def test_the_forms_the_line_knows(self):
        new, sec = self.without_consent()
        ok = {"outbounds": [{"tag": "direct", "protocol": "freedom"},
                            {"tag": "gx-1", "protocol": "freedom", "streamSettings": {"sockopt": {"mark": 257}}}],
              "routing": {"rules": [{"inboundTag": ["gw-dns-int"], "outboundTag": "direct"},          # no matcher
                                    {"inboundTag": ["socks-in"], "user": ["a"], "outboundTag": "direct"},
                                    {"inboundTag": ["socks-in"], "domain": PLAIN, "outboundTag": "gx-1"}]}}
        xray.ru_guard(new, ok)                                                # an AWG leg is abroad, no matcher is a route
        for rule in ({"inboundTag": ["socks-in"], "user": ["a"], "domain": PLAIN, "outboundTag": "direct"},
                     {"domain": ["regexp:\\.ru$"], "outboundTag": "direct"},                  # every inbound
                     {"inboundTag": ["gw-vless"], "ip": ["geoip:ru"], "outboundTag": "direct"}):
            cfg = json.loads(json.dumps(ok))
            cfg["routing"]["rules"].append(rule)
            with self.assertRaises(KaskadError):
                xray.ru_guard(new, cfg)
            with open(self.path(CONF), "w", encoding="utf-8") as f:              # the watchdog never starts it
                json.dump(cfg, f)
            self.assertTrue(xray.unsafe_file(new, sec, self.w.runner, exits.pool(self.w.runner)), rule)

    def test_with_the_consent_a_plain_rule_is_the_choice_of_the_code(self):
        st = self.socks_world()
        cfg = {"outbounds": [{"tag": "direct", "protocol": "freedom"}],
               "routing": {"rules": [{"inboundTag": ["socks-in"], "user": ["a"], "domain": PLAIN,
                                      "outboundTag": "direct"}]}}
        xray.ru_guard(st, cfg)                                                # the line asks the node only


class HookRuOnlyTest(R2Case):
    def test_the_hook_puts_no_ruonly_without_the_consent(self):
        # -sec-2 П-1: gw_ruonly of a hook passes the last line too — a plain block instead
        new, sec = self.without_consent()
        net.set_element(self.w.runner, new, "10.77.1.10", exits.MARK_BLOCK, ruonly=True)
        self.assertNotIn("10.77.1.10", self.w.nft.elements("gw_ruonly"))
        self.assertEqual(ruguard.tripped()["where"], "nft")
        st = self.socks_world()
        net.set_element(self.w.runner, st, "10.77.1.11", exits.MARK_BLOCK, ruonly=True)
        self.assertIn("10.77.1.11", self.w.nft.elements("gw_ruonly"))

