"""C7 (k3/handoff/SPEC-node-score.md §6.3, NS-D6; RESEARCH §5.2): the rule «канал у потолка порта» of ``HOST_RULES`` —
the busier direction of the interfaces of the default route ÷ the port of the TARIFF (C1), 1 min, > 90 % → < 85 %;
no port typed in (or only the inventory) — not checked, never a guess from ethtool / /sys speed."""

import unittest

from kaskad import state
from kaskad.gw import hostmon as H
from kaskad.gw import model
from tests.helpers import RootCase

MB = 1e6 / 8                   # bytes/s of 1 Mbit/s


class PortRuleTest(unittest.TestCase):
    def run_rounds(self, rates, port=100, st=None, t0=1000.0):
        """Rounds every 10 s with (rx Mbit/s, tx Mbit/s) -> (state, events, the rule's state of the last round)."""
        cfg = {"enabled": True, "probes": False, "anchors": [], "port_mbit": port}
        evs = []
        mono = t0
        for rx, tx in rates:
            mono += 10
            st, ev = H.evaluate(st or {}, {"mono": mono, "rx_rate": rx * MB, "tx_rate": tx * MB}, cfg,
                                {"owner": "collector"}, 1792000000 + mono, mono)
            evs += [e for e in ev if e["args"]["check"] == "port"]
        return st, evs, st["rules"]["port"]

    def test_the_rule_is_in_the_table(self):
        r = H.RULES["port"]
        self.assertEqual((r["group"], r["window_s"], r["warn"], r["crit"], r["alarm"]), ("net", 60, [90, 85], None, None))
        self.assertEqual(H.kind_of(r), "host_net")
        self.assertIn("no_port", H.REAL_WHY)

    def test_no_port_is_not_checked(self):
        for port in (None, 0, -5, True, "1000"):
            _st, evs, rs = self.run_rounds([(99, 99)] * 8, port=port)
            self.assertEqual((rs["level"], rs["why"]), ("na", "no_port"), port)
            self.assertEqual(evs, [])

    def test_waits_for_its_minute_then_warns_with_the_port_in_words(self):
        st, evs, rs = self.run_rounds([(10, 95)] * 3)
        self.assertEqual(rs["level"], "wait")
        st, evs, rs = self.run_rounds([(10, 95)] * 4, st=st, t0=1030.0)
        self.assertEqual(rs["level"], "warn")
        self.assertEqual(rs["value"], 95.0)
        self.assertEqual(rs["find"]["args"]["port"], {"key": "gw_host.n.port_mbit", "args": {"v": 100}})
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["kind"], evs[0]["key"], evs[0]["args"]["push"]), ("host_net", "gw_host.event.raise",
                                                                                   False))
        self.assertEqual(H.say(rs["find"], "ru"), "Канал занят на 95% скорости порта по тарифу (100 Мбит/с) уже 1 мин")
        self.assertEqual(H.say(rs["find"], "en"),
                         "The channel has used 95% of the plan’s port speed (100 Mbit/s) for 1 min")

    def test_hysteresis_90_on_85_off_and_the_busier_direction(self):
        st, _e, rs = self.run_rounds([(90, 1)] * 7)
        self.assertEqual(rs["level"], "ok")                        # exactly 90 — not above
        st, _e, rs = self.run_rounds([(91, 1)] * 7, st=st, t0=1070.0)
        self.assertEqual(rs["level"], "warn")                      # the download alone
        st, _e, rs = self.run_rounds([(86, 1)] * 7, st=st, t0=1140.0)
        self.assertEqual(rs["level"], "warn")                      # not yet back under 85
        st, evs, rs = self.run_rounds([(84, 1)] * 7, st=st, t0=1210.0)
        self.assertEqual(rs["level"], "ok")

    def test_gigabit_port_and_the_average_of_the_minute(self):
        # 6 rounds of 1000 and 1 of 0: the mean 857 of 1000 — not above 90 %
        _st, _e, rs = self.run_rounds([(0, 1000)] * 6 + [(0, 0)], port=1000)
        self.assertEqual(rs["level"], "ok")
        _st, _e, rs = self.run_rounds([(0, 950)] * 7, port=1000)
        self.assertEqual(rs["find"]["args"]["port"], {"key": "gw_host.n.port_gbit", "args": {"v": 1}})
        self.assertEqual(H.port_text(2500), {"key": "gw_host.n.port_gbit", "args": {"v": 2.5}})
        self.assertEqual(H.port_text(1050), {"key": "gw_host.n.port_mbit", "args": {"v": 1050}})

    def test_a_short_burst_is_not_an_alarm(self):
        # REV-ns-c1 З-4: the mean of the minute, not the last point — 6 rounds of 0 and ONE of 1000 at a port of 1000
        # (the last point alone would be 100 %)
        _st, evs, rs = self.run_rounds([(0, 0)] * 6 + [(1000, 1000)], port=1000)
        self.assertEqual((rs["level"], rs["value"]), ("ok", 16.7))
        self.assertEqual(evs, [])
        _st, evs, rs = self.run_rounds([(950, 0)] * 7, port=1000)  # 95 % for the whole minute — a warning
        self.assertEqual((rs["level"], rs["value"]), ("warn", 95.0))
        self.assertEqual(len(evs), 1)


class PortOfTheTariffTest(RootCase):
    """``model.hostmon`` gives the rule the port of the tariff: typed in or a catalog — yes; a bad tariff — None."""

    def save_tariff(self, raw):
        st = state.empty()
        st["gateway"] = {"enabled": True, "hostmon": {"tariff": raw}}
        state.save(st)
        return model.hostmon(state.load())["port_mbit"]

    def test_the_port_reaches_the_rule(self):
        self.assertEqual(self.save_tariff({"v": 1, "vals": {"port_mbit": {"v": 1000, "src": "user"}}}), 1000)
        self.assertEqual(self.save_tariff({"v": 1, "vals": {"port_mbit": {"v": 500, "src": "catalog"}}}), 500)
        for bad in ({"v": 1, "vals": {"port_mbit": {"v": 10000, "src": "inventory"}}},
                    {"v": 1, "vals": {"port_mbit": {"v": "1000", "src": "user"}}}, [], "x", None):
            self.assertIsNone(self.save_tariff(bad), bad)


if __name__ == "__main__":
    unittest.main()
