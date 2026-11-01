"""Findings of the stand T-G1 (G1, Ubuntu 24.04, 3.2.8), fixed together:

    C3-1  an AmneziaWG exit was dead only 2–3 minutes after its far end went: its last handshake stayed «fresh».
          Now a connection through the leg itself every round, dead after 3 rounds without an answer (the next
          round in 5 s while it fails: 20–35 s), whatever the handshake; an idle exit, one that cuts ICMP, a
          short loss stay alive; the screens tell the age of the handshake and «нет ответа 35 с — клиенты
          переходят на другой выход»
    B1-4  keys without a name of their own were all «amneziawg»: now «AmneziaWG 2.0 · 203.0.113.141:51820»
    R-1   after a plain reboot the journal read like a breakdown («NET repair … 26 расхождений», «GW legs … код 1»):
          what a reboot always loses is put back with one calm line; what fails is still news
    D3-3  after ``nft delete table ip kaskad`` the networks of Russia came back only with the 5-minute timer and the
          panel kept showing «загружены» of the gone table
"""

import json
import os
import socket
import time
import unittest
from unittest import mock

from kaskad import boot, compat, netcheck, nft, state
from kaskad.gw import collect, exits, live, model, xray
from kaskad.rules import apply as rapply
from kaskad.rules import ops as rops
from kaskad.rules import timer as rtimer
from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, install_parser, write_pool
from tests.gwcase import GwCase
from tests.test_gw_ops import CliGwCase
from tests.test_rules_gw import RulesGwCase


# ── C3-1: the connections through a leg ────────────────────────────────────

class ConnectThroughTest(unittest.TestCase):
    """``exits.connect_through`` on real sockets of this machine (SO_MARK needs root: the mark is not set here)."""

    def setUp(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.closed = s.getsockname()[1]
        s.close()
        self._p = mock.patch.object(exits, "_mark", lambda s, m: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.srv.close()

    def test_one_answering_target_is_enough(self):
        got = exits.connect_through([0x102, 0x103], targets=(("127.0.0.1", self.closed), ("127.0.0.1", self.port)),
                                    timeout=2)
        self.assertEqual(got, {0x102: True, 0x103: True})

    def test_a_refusal_is_no_answer(self):
        self.assertEqual(exits.connect_through([0x102], targets=(("127.0.0.1", self.closed),), timeout=0.5),
                         {0x102: False})

    def test_silent_legs_cost_one_wait_together(self):
        t = time.monotonic()
        got = exits.connect_through([0x102, 0x103, 0x104], targets=(("192.0.2.1", 443),), timeout=0.4)   # TEST-NET-1
        self.assertEqual(got, {0x102: False, 0x103: False, 0x104: False})
        self.assertLess(time.monotonic() - t, 1.0)                       # not 3 × 0.4 s

    def test_a_socket_that_cannot_be_marked_tells_nothing(self):
        def deny(s, m):
            raise PermissionError(1, "Operation not permitted")
        with mock.patch.object(exits, "_mark", deny):
            self.assertEqual(exits.connect_through([0x102], targets=(("127.0.0.1", self.port),)), {0x102: None})
        self.assertEqual(exits.connect_through([]), {})


class AwgExitDeathTest(CliGwCase):
    """A device pinned to the AmneziaWG exit (fail policy «block»): where its packets go while the exit dies."""

    def setUp(self):
        super().setUp()
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.ok("gw", "route", "exit:" + self.aid)

    def round(self, dt=10):
        res = collect.tick(self.w.runner)["exits"]
        self.now += dt
        return res

    def test_dead_when_half_of_the_checks_fail_whatever_the_handshake(self):
        """3.3.5 (owner 15.10): dead when half of the checks of the last 2 min failed — as many failed as good ones."""
        self.w.leg(2, handshake=600)                    # idle: no handshake for 10 minutes — but it answers
        for _ in range(3):
            self.assertEqual(self.round()["changes"], [])
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})
        good = len(exits.load_snapshot()["exits"][self.aid]["tries"])
        self.assertGreaterEqual(good, 3)
        self.w.leg(2, handshake=3, probe=False)         # the far end is gone, its last handshake is «fresh»
        for i in range(2 * (good - 1)):                  # rounds of 5 s; a recheck replaces the last check (one per
            r = self.round(5)                            # 10 s): `good` failed checks take 2·(good−1)+1 rounds
            self.assertEqual((r["changes"], r["failing"]), ([], True), i)
            self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})    # fewer than half: nobody moves
        r = self.round(5)
        self.assertEqual(r["changes"], [{"id": self.aid, "was": True, "alive": False}])
        self.assertFalse(r["failing"])                   # dead: rounds of 10 s again
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x2ff})       # its fail policy, no leak
        self.assertIn("GW exit %s (Amsterdam): не работает — нет ответа %d с, рукопожатие 3 с назад"
                      % (self.aid, 10 * (good - 1)), self.log())
        self.assertEqual(self.w.connects[-1], [0x102])  # a connection through the leg every round (no ping, no curl)
        self.w.leg(2, handshake=0)
        mine = lambda r: [c for c in r["changes"] if c["id"] == self.aid]   # (VLESS: xray silent — its own story)
        self.assertEqual(mine(self.round()), [])         # one answer: not back yet
        self.assertEqual(mine(self.round()), [{"id": self.aid, "was": False, "alive": True}])   # two in a row
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})

    def test_a_short_loss_moves_nobody(self):
        self.w.leg(2)
        for _ in range(4):
            self.round()
        self.w.leg(2, probe=False)
        self.round(5)
        self.round(5)
        self.w.leg(2)
        r = self.round()
        self.assertEqual((r["changes"], r["failing"]), ([], False))
        rec = exits.load_snapshot()["exits"][self.aid]
        self.assertEqual((rec["alive"], rec["fails"], "fail_since" in rec), (True, 0, False))
        self.assertNotIn("не работает", self.log())

    def test_a_failing_exit_is_checked_again_in_5_seconds(self):
        waits = []
        self.w.leg(2, probe=False)

        def pause(runner, s):
            waits.append(s)
            self.now += s
        with mock.patch.object(collect, "pause", pause):
            collect.loop(self.w.runner, interval=10, rounds=10)
        self.assertEqual(waits[0], 5)                    # failing: in 5 s (it replaces the last check, 3.3.5)
        self.assertEqual(waits[-2:], [10, 10])           # dead: 10 s again
        self.assertIn("не работает", self.log())


class ExitTextsTest(CliGwCase):
    """«Состояние» (17 → 5), «Выходы» (gw exits) and live.json: the handshake and the silence."""

    def setUp(self):
        super().setUp()
        self._p = mock.patch.object(live, "_now", lambda: self.now)
        self._p.start()
        self.enabled_world()
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}          # the VLESS exit works: somewhere to move to

    def tearDown(self):
        self._p.stop()
        super().tearDown()

    def exit_line(self, out):
        return [l for l in out.splitlines() if "Amsterdam" in l][0].strip()

    def test_handshake_silence_and_where_the_clients_go(self):
        self.w.leg(2, handshake=40)
        collect.tick(self.w.runner)
        out, _ = self.ok("gw", "status")
        self.assertEqual(self.exit_line(out),
                         "Amsterdam (AmneziaWG 1.0 · 198.51.100.31:51820) — работает, рукопожатие 40 с назад")
        self.w.leg(2, handshake=55, probe=False)
        self.now += 10                                                # (3.3.5: a recheck within 10 s replaces a check)
        collect.tick(self.w.runner)
        self.now += 7
        out, _ = self.ok("gw", "status")
        # 1 failed of 2 checks: half, but fewer than 4 — «проверяется», not «работает» (REV-335 №4)
        self.assertEqual(self.exit_line(out), "Amsterdam (AmneziaWG 1.0 · 198.51.100.31:51820) — проверяется: нет ответа 7 с "
                                              "(не прошло проверок: 1 из 2; решение — от 4 проверок); рукопожатие 62 с назад")
        d = json.loads(self.read("/run/kaskad/live.json"))["exits"][self.aid]
        self.assertEqual((d["handshake_at"], d["silent_since"], d["fails"], d["dies_after"], d["detail"]),
                         (int(self.now - 7 - 55), int(self.now - 7), 1, 2, "нет ответа"))
        self.now += 3
        for _ in range(2):                                            # 3 failed of 4 checks: dead
            collect.tick(self.w.runner)
            self.now += 10
        self.now -= 3
        out, _ = self.ok("gw", "exits")
        self.assertIn("Amsterdam (AmneziaWG 1.0 · 198.51.100.31:51820) — не работает: нет ответа 27 с, рукопожатие 62 с "
                      "назад — клиенты переходят на другой выход", out)
        self.assertNotIn("ног", out)
        rc, doc, _ = self.run_json("gw", "exits")
        row = [r for r in doc["exits"] if r["id"] == self.aid][0]
        self.assertEqual((row["state"], row["silent"], row["handshake"], row["others"], row["named"], row["where"]),
                         ("dead", 27, 62, 1, True, "AmneziaWG 1.0 · 198.51.100.31:51820"))
        self.w.curl.observatory = {"gx-" + self.vid: False}         # nothing else works either: 4 good, 4 failed
        for _ in range(4):
            collect.tick(self.w.runner)
            self.now += 10
        out, _ = self.ok("gw", "status")
        self.assertTrue(self.exit_line(out).endswith("— других рабочих выходов нет"), out)

    def test_without_a_fresh_live_snapshot_no_handshake_is_made_up(self):
        self.w.leg(2, handshake=40)
        collect.tick(self.w.runner)
        self.now += live.STALE + 1                       # the collector stopped writing
        out, _ = self.ok("gw", "status")
        self.assertEqual(self.exit_line(out), "Amsterdam (AmneziaWG 1.0 · 198.51.100.31:51820) — работает")


# ── B1-4: names ────────────────────────────────────────────────────────────

class ExitNamesTest(GwCase):
    def test_keys_without_a_name_are_called_by_kind_and_address(self):
        install_parser()
        plain = VLESS_A.split("#")[0]                                         # vless:// without a fragment
        a1 = awg_line(2, name="amneziawg")                                   # .conf without a name
        a3 = awg_line(3, name="amneziawg", line="3.1")
        a4 = awg_line(4, name="amnezia", endpoint="198.51.100.31:51831")     # vpn:// without a description
        write_pool([plain, a1, a3, a4, VLESS_B])
        pool = exits.pool(self.w.runner)
        ids = [e["id"] for e in pool]
        st = {"gateway": {"exits": {ids[4]: {"enabled": True, "name": "Дача"}}}}
        rows = [exits.public(e, st) for e in pool]
        self.assertEqual([(r["name"], r["named"]) for r in rows], [
            ("VLESS · 198.51.100.20:443", False),
            ("AmneziaWG 1.0 · 198.51.100.31:51820", False),          # always with the port (T-X6: one rule for all)
            ("AmneziaWG 3.1 · 198.51.100.31:51820", False),
            ("AmneziaWG 1.0 · 198.51.100.31:51831", False),
            ("Дача", True)])                                          # the name given in Kaskad wins over «Riga»
        self.assertEqual(rows[4]["where"], "VLESS · 198.51.100.21:8443")
        self.assertEqual(exits.label({}, pool[4]), "Riga")            # the key's own name, when Kaskad gave none
        self.assertEqual(len(set(r["name"] for r in rows)), 5)


class StateTextTest(unittest.TestCase):
    def test_texts(self):
        t = exits.state_text
        self.assertEqual(t({"state": "alive", "usable": True, "detail": "отвечает", "handshake": 40}),
                         "работает, рукопожатие 40 с назад")
        self.assertEqual(t({"state": "alive", "usable": True, "detail": "отвечает", "handshake": 130}),
                         "работает, рукопожатие 2 мин назад")
        self.assertEqual(t({"state": "alive", "usable": True, "detail": "нет ответа (не прошло проверок: 2 из 5 за 2 мин)",
                            "silent": 15, "fails": 2, "dies_after": 5}),
                         "работает, но нет ответа 15 с (не прошло проверок: 2 из 5 за 2 мин)")
        self.assertEqual(t({"state": "dead", "detail": "нет ответа", "silent": 35, "others": 2, "enabled": True}),
                         "не работает: нет ответа 35 с — клиенты переходят на другой выход")
        self.assertEqual(t({"state": "dead", "detail": "сервер выхода не отвечает", "silent": 40, "others": 0}),
                         "не работает: сервер выхода не отвечает (уже 40 с) — других рабочих выходов нет")
        self.assertEqual(t({"state": "dead", "detail": "нет интерфейса kawg2"}), "не работает: нет интерфейса kawg2")
        self.assertEqual(t({"state": "unknown", "detail": exits.NOT_CHECKED}), "ещё не проверялся")
        self.assertEqual(exits.ago_text(3725), "1 ч 02 мин")

    def test_the_silence_starts_when_the_failed_probe_did(self):
        snap = {"exits": {"e-1": {"alive": True, "detail": "отвечает", "since": 10, "fails": 0}}}
        new, _, _ = exits.merge(snap, {"e-1": {"alive": False, "detail": "нет ответа", "dies_after": 3, "waited": 4}},
                                ["e-1"], now=100)
        self.assertEqual((new["exits"]["e-1"]["fail_since"], new["exits"]["e-1"]["alive"]), (96, True))
        row = exits.public({"id": "e-1", "kind": "awg", "where": "AmneziaWG 2.0 · 198.51.100.31"}, {}, new)
        self.assertEqual((row["fails"], row["dies_after"], row["silent_since"]), (1, 1, 96))    # 1 failed of 1 check


# ── R-1: after a reboot ────────────────────────────────────────────────────

class AfterRebootTest(CliGwCase):
    def setUp(self):
        super().setUp()
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        os.unlink(self.path("/var/log/kaskad.log"))

    def up(self, seconds):
        self.write("/proc/uptime", "%.2f 100.00\n" % seconds)

    def reboot(self):
        """What a reboot loses: ip rules and routes (here as networkd takes them), the iptables rules; the boot file
        of the table holds the map of the last apply, older than the exits watchdog's."""
        self.w.networkd_restart()
        compat.remove(self.w.runner)
        compat.remove_in(self.w.runner)
        self.w.nft.tables["kaskad"]["sets"]["gw_route"]["elems"]["10.77.1.10"] = exits.MARK_BLOCK
        self.up(40)

    def test_the_restore_after_a_reboot_is_one_calm_line(self):
        self.reboot()
        res = netcheck.check(self.w.runner, repair=True)
        self.assertTrue(res["ok"], res)
        log = self.log()
        self.assertIn("NET после перезагрузки: правила шлюза восстановлены (маршруты устройств в таблице nftables, "
                      "правило совместимости iptables, маршрутизация шлюза, правило совместимости шлюза для UFW)", log)
        self.assertNotIn("repair", log)
        self.assertNotIn("расхождени", log)
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": 0x102})          # and it is all back
        self.assertTrue(compat.present(self.w.runner) and compat.present_in(self.w.runner))
        self.assertFalse(os.path.exists(boot.mark_path()))
        res = collect.tick(self.w.runner)["exits"]               # the collector's first round: the legs' rules
        self.assertEqual((res["legs"]["broken"], res["legs"]["ok"]), (["kawg2"], True))
        self.assertEqual(res["repaired"], [])                    # a leg's rule is born in its slot (T-3379 З-2)
        self.assertIn("GW после перезагрузки: выходы AmneziaWG подняты (kawg2)", self.log())
        self.assertNotIn("GW после перезагрузки: правила шлюза восстановлены", self.log())   # nothing to move
        self.assertNotIn("GW legs", self.log())
        self.assertNotIn("repair", self.log())
        self.assertTrue(os.path.exists(boot.mark_path()))        # nothing moved: the restore is over in this round
        self.assertFalse(boot.pending())
        collect.tick(self.w.runner)
        self.assertTrue(os.path.exists(boot.mark_path()))
        self.w.networkd_restart()                               # from now on a lost rule is news again
        netcheck.check(self.w.runner, repair=True)
        self.assertIn("GW repair: ", self.log())

    def test_what_cannot_be_repaired_is_still_told(self):
        self.reboot()
        self.w.ipt.fail_insert = "iptables: Index of insertion too big.\n"
        res = netcheck.check(self.w.runner, repair=True)
        bad = [p for p in res["problems"] if not p["repaired"]]
        self.assertFalse(res["ok"])
        self.assertTrue(bad and all(p.get("error") for p in bad), res["problems"])
        self.assertNotIn("правило совместимости iptables", self.log())          # not «restored» when it is not
        rc, out, err = self.run_cli("net", "check", "--repair", "--quiet")
        self.assertIn("Не исправлено", err)

    def test_the_collector_after_a_reboot(self):
        self.up(30)
        self.w.networkd_restart()
        res = collect.tick(self.w.runner)["exits"]
        self.assertEqual(res["repaired"], ["routing"])
        log = self.log()
        self.assertIn("GW после перезагрузки: правила шлюза восстановлены (маршрутизация шлюза)", log)
        self.assertIn("GW после перезагрузки: выходы AmneziaWG подняты (kawg2)", log)
        self.assertNotIn("GW repair", log)
        self.assertFalse(os.path.exists(boot.mark_path()))      # a round that had to repair is not «whole»
        collect.tick(self.w.runner)
        self.assertTrue(os.path.exists(boot.mark_path()))
        self.w.networkd_restart()
        collect.tick(self.w.runner)
        self.assertIn("GW repair: маршрутизация шлюза", self.log())
        self.assertIn("GW legs: ноги AmneziaWG в порядке", self.log())

    def test_a_leg_still_starting_is_no_alarm_but_one_that_stays_down_is(self):
        pool = exits.pool(self.w.runner)
        self.up(25)
        self.w.kill_leg(2)
        self.w.script.fail_legs.add(2)                           # its unit is still starting — or it never will
        for _ in range(2):
            res = exits.ensure_legs(self.w.runner, pool)
            self.assertEqual((res["healed"], res["ok"]), (True, False))
            self.now += exits.HEAL_RETRY
        self.assertNotIn("не привёл", self.log())
        self.assertIn("boot", self.read("/run/kaskad/gw/legs-heal.stamp").split())
        self.up(boot.WINDOW + 60)                                # the restore after the reboot is long over
        exits.ensure_legs(self.w.runner, pool)
        self.now += exits.HEAL_RETRY
        exits.ensure_legs(self.w.runner, pool)
        self.assertEqual(self.log().count("GW legs: gokaskad --egress-awg-heal (код 1) не привёл ноги AmneziaWG в "
                                          "порядок"), 1)          # news — once


# ── D3-3: the networks of Russia after the table was put back ──────────────

class RulesAfterRestoreTest(RulesGwCase):
    def setUp(self):
        super().setUp()
        self.st = self.ru_state()
        nft.apply(self.st, self.w.runner)
        rtimer.ensure(self.w.runner)
        self.assertEqual(rapply.ensure_kernel(self.w.runner, self.st)["action"], "loaded")
        self.assertIs(rops._table_status(None)["loaded"], True)

    def started(self):
        return [c for c in self.w.sysd.calls if c[1:] == ["start", "--no-block", rtimer.LOAD_SERVICE]]

    def test_the_collector_loads_them_at_once_and_the_panel_is_told_the_truth(self):
        self.w.nft.flush_ruleset()                               # «nft delete table ip kaskad»
        res = collect.tick(self.w.runner)["exits"]
        self.assertIn("table", res["repaired"])
        self.assertTrue(res["rules_load"])
        self.assertEqual(len(self.started()), 1)                 # the load now, not in 5 minutes
        t = rops._table_status(None)                             # the panel: files only
        self.assertIs(t["loaded"], False)
        self.assertEqual(rops.table_text(t), "не загружены — загрузит kaskad-gw-rules-load.timer (или kaskadctl "
                                             "rules load)")
        rc, out, _ = self.run_cli("gw", "check")
        self.assertIn("сети России не загружены в таблицу\n", out)          # no «(ошибка; …)» — nothing failed
        rc, doc, _ = self.run_json("rules", "load")              # what the started service runs
        self.assertEqual(doc["action"], "loaded")
        self.assertIs(rops._table_status(None)["loaded"], True)
        self.assertFalse(rtimer.after_restore(self.w.runner))    # the lists are in the table: nothing to start
        self.assertEqual(len(self.started()), 1)

    def test_the_minute_watchdog_does_the_same(self):
        self.w.sysd.units[collect.UNIT] = {"enabled": True, "active": False, "masked": True}
        self.w.nft.flush_ruleset()
        netcheck.check(self.w.runner, repair=True)
        self.assertEqual(len(self.started()), 1)
        self.assertIs(rops._table_status(None)["loaded"], False)

    def test_without_lists_nothing_is_started(self):
        with mock.patch.object(rapply, "kernel_meta", lambda v=None: {"ready": False, "count": 0, "sha256": None}):
            self.w.nft.flush_ruleset()
            collect.tick(self.w.runner)
        self.assertEqual(self.started(), [])
        self.assertIs(rops._table_status(None)["loaded"], True)   # the state file of the last load is untouched


if __name__ == "__main__":
    unittest.main()
