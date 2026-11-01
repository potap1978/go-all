"""kaskad.gw.exits — the pool through egress_parse.py, liveness of the legs, the snapshot, the marks."""

import json
import os

from kaskad.errors import KaskadError
from kaskad.gw import exits, model
from tests.fakes_gw import T0, VLESS_A, VLESS_B, awg_line, install_parser, write_pool
from tests.gwcase import GwCase


class PoolTest(GwCase):
    def test_parser_from_install_sh_and_the_pool(self):
        install_parser()
        awg = awg_line(2, name="Amsterdam")
        awg31 = awg_line(4, name="Riga3", line="3.1")
        write_pool([VLESS_A, awg, "", "garbage-line", awg31, VLESS_B])
        pool = exits.pool(self.w.runner)
        self.assertEqual([e["index"] for e in pool], [1, 2, 3, 4, 5])              # blank lines are not counted
        self.assertEqual([e["kind"] for e in pool], ["xray", "awg", "bad", "awg", "xray"])
        a = pool[1]
        self.assertEqual((a["mark"], a["iface"], a["table"], a["version"]), (258, "kawg2", 258, "1.0"))
        self.assertEqual(a["outbound"]["streamSettings"]["sockopt"]["mark"], 258)
        self.assertEqual(a["outbound"]["tag"], "gx-" + a["id"])
        self.assertEqual(pool[3]["version"], "3.1")
        self.assertEqual(pool[3]["iface"], "kawg4")
        v = pool[0]
        self.assertEqual((v["mark"], v["iface"]), (0x301, None))                 # VLESS of line 1: 0x300+1
        self.assertEqual((pool[4]["mark"], exits.tproxy_port(pool[4])), (0x305, 12405))   # line 5: its own port
        self.assertEqual(exits.tproxy_tag(v["id"]), "tproxy-" + v["id"])
        self.assertEqual(v["outbound"]["protocol"], "vless")
        self.assertEqual(v["outbound"]["streamSettings"]["sockopt"]["tcpMaxSeg"], exits.OUTBOUND_MSS)
        self.assertEqual(v["name"], "Vienna")
        self.assertEqual(v["id"], exits.exit_id(VLESS_A))
        self.assertTrue(v["id"].startswith("e-") and len(v["id"]) == 12)
        self.assertEqual(pool[2]["problem"], "неизвестный формат ключа")
        # nothing secret in the public view
        pub = json.dumps([exits.public(e) for e in pool], ensure_ascii=False)
        for secret in ("11111111-2222", "PrivateKey", "awg://", "vless://", "pbk"):
            self.assertNotIn(secret, pub)

    def test_ids_survive_a_removed_line(self):
        install_parser()
        awg = awg_line(2)
        write_pool([VLESS_A, awg])
        before = dict((e["id"], e["index"]) for e in exits.pool(self.w.runner))
        write_pool([awg])
        after = dict((e["id"], e["index"]) for e in exits.pool(self.w.runner))
        aid = exits.exit_id(awg)
        self.assertEqual((before[aid], after[aid]), (2, 1))
        self.assertEqual(exits.pool(self.w.runner)[0]["iface"], "kawg1")

    def test_missing_parser_is_fetched_once_through_gokaskad(self):
        write_pool([VLESS_A])
        pool = exits.pool(self.w.runner)
        self.assertEqual(pool[0]["kind"], "xray")
        self.assertEqual(self.w.script.calls, [["--egress-ensure-parser"]])

    def test_no_parser_no_runner_is_an_error_but_empty_pool_needs_none(self):
        self.assertEqual(exits.pool(), [])
        write_pool([VLESS_A])
        with self.assertRaises(KaskadError):
            exits.pool()
        got = exits.current({})[0]                                               # renderers never raise:
        self.assertEqual([(e["kind"], e["mark"], e["outbound"]) for e in got], [("xray", 0x301, None)])
        self.assertIn("разборщик", got[0]["problem"])                             # kinds by the prefix

    def test_old_parser_is_refused(self):
        p = self.path("/etc/kaskad/egress/egress_parse.py")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("def vless_outbound(*a):\n    return None\n")
        write_pool([VLESS_A])
        with self.assertRaises(KaskadError) as cm:
            exits.pool()
        self.assertIn("старый", str(cm.exception))


class ProbeTest(GwCase):
    def setUp(self):
        super().setUp()
        install_parser()
        self.awg2 = awg_line(2)
        self.awg3 = awg_line(3)
        write_pool([VLESS_A, self.awg2, self.awg3])
        self.pool = exits.pool(self.w.runner)
        self.vid, self.a2, self.a3 = [e["id"] for e in self.pool]

    def test_awg_legs(self):
        # liveness is a connection through the leg itself every round; the handshake is only told (C3-1)
        self.w.leg(2, handshake=30)
        self.w.leg(3, handshake=600, probe=True)                # idle: its last handshake 10 min ago — it answers
        res = exits.probe(self.w.runner, self.pool)
        self.assertEqual(res[self.a2], {"alive": True, "detail": "отвечает", "handshake": 30, "dies_after": 3})
        self.assertEqual(res[self.a3], {"alive": True, "detail": "отвечает", "handshake": 600, "dies_after": 3})
        self.assertEqual(self.w.connects, [[0x102, 0x103]])     # both legs at once, one round
        self.w.leg(3, handshake=5, probe=False)                 # a fresh handshake does not make it alive
        res = exits.probe(self.w.runner, self.pool)
        self.assertEqual(res[self.a3], {"alive": False, "detail": "нет ответа", "handshake": 5, "dies_after": 3,
                                        "waited": 0})                    # how long its connections waited (s)
        self.w.kill_leg(2)
        res = exits.probe(self.w.runner, self.pool)
        self.assertEqual(res[self.a2], {"alive": False, "detail": "нет интерфейса kawg2", "handshake": None,
                                        "dies_after": 3})
        self.assertEqual(self.w.connects[-1], [0x103])          # no connection through a leg that is not there
        self.assertEqual(res[self.vid].get("dies_after"), None)                 # VLESS: xray's observatory, 2

    def test_vless_by_the_observatory(self):
        res = exits.probe(self.w.runner, self.pool)
        self.assertEqual(res[self.vid], {"alive": None, "detail": "xray шлюза не отвечает"})       # unknown
        self.w.sysd.units["kaskad-gw-xray.service"] = {"enabled": True, "active": True}
        res = exits.probe(self.w.runner, self.pool)
        self.assertEqual(res[self.vid], {"alive": None, "detail": "xray ещё не проверил"})        # unknown
        self.w.curl.observatory = {"gx-" + self.vid: True}
        self.assertEqual(exits.probe(self.w.runner, self.pool)[self.vid],
                         {"alive": True, "detail": "сервер выхода отвечает", "try_at": int(self.now)})
        self.w.curl.observatory = {"gx-" + self.vid: False}
        self.assertEqual(exits.probe(self.w.runner, self.pool)[self.vid],          # 3.3.5: xray's why, its probe
                         {"alive": False, "detail": "сервер выхода не отвечает", "try_at": int(self.now),
                          "obs_delay": 5000, "obs_error": "context deadline exceeded"})

    def test_vless_dead_right_after_an_xray_restart_is_unknown(self):
        mono = self.now - T0 + 1000.0                          # exits._mono in GwCase
        self.w.sysd.units["kaskad-gw-xray.service"] = {"enabled": True, "active": True, "since_mono": mono - 10}
        self.w.curl.observatory = {"gx-" + self.vid: False}
        res = exits.probe(self.w.runner, self.pool)[self.vid]
        self.assertIsNone(res["alive"])                         # 10 s after the start: not believed yet
        self.assertIn("запущен 10 с назад", res["detail"])
        self.assertIn(["systemctl", "show", "-p", "ActiveEnterTimestampMonotonic", "--value",
                       "kaskad-gw-xray.service"], self.w.sysd.calls)
        self.w.sysd.units["kaskad-gw-xray.service"]["since_mono"] = mono - exits.XRAY_GRACE
        self.assertIs(exits.probe(self.w.runner, self.pool)[self.vid]["alive"], False)
        self.w.sysd.units["kaskad-gw-xray.service"]["since_mono"] = mono - 5
        self.w.curl.observatory = {"gx-" + self.vid: True}     # "alive" is believed at once
        self.assertIs(exits.probe(self.w.runner, self.pool)[self.vid]["alive"], True)

    def test_snapshot_merge(self):
        """3.3.5, the rule of the owner (15.10): «упал» — at least half of the checks of the last 2 min failed, 4 checks
        at the least; back after 2 good checks in a row; the window starts anew on every change."""
        ids = [e["id"] for e in self.pool]
        self.w.leg(2)
        probed = exits.probe(self.w.runner, self.pool)          # VLESS: xray does not answer (unknown)
        snap, changes, dirty = exits.merge(exits.load_snapshot(), probed, ids, now=100)
        self.assertTrue(dirty)
        # a new dead leg is «проверяется» until the rule says «упал» (T-M6-qs330d №4): nothing to tell yet
        self.assertEqual(sorted(changes), sorted([(self.a2, None, True)]))
        self.assertEqual(exits.alive_ids(snap), {self.a2})
        self.assertEqual(exits.state_of(snap["exits"][self.a3]), "unknown")
        self.assertEqual(snap["exits"][self.vid]["alive"], False)                # new and unknown: not usable
        exits.save_snapshot(snap)
        for t in (110, 120):
            snap, changes, dirty = exits.merge(exits.load_snapshot() if t == 110 else snap, probed, ids, now=t)
            self.assertEqual((changes, dirty), ([], True))      # its failures are recorded, 3 checks < 4
        self.assertEqual((snap["exits"][self.a3]["fails"], snap["exits"][self.a3]["checks"]), (3, 3))
        snap, changes, dirty = exits.merge(snap, probed, ids, now=125)
        self.assertEqual((changes, dirty), ([], False))         # a recheck 5 s later replaces the last check (3.3.5)
        self.assertEqual(snap["exits"][self.a3]["checks"], 3)
        snap, changes, dirty = exits.merge(snap, probed, ids, now=130)
        self.assertEqual((changes, dirty), ([(self.a3, None, False)], True))  # the fourth: dead, told once
        self.assertEqual(exits.state_of(snap["exits"][self.a3]), "dead")
        self.assertEqual(snap["exits"][self.a3]["fail_since"], 100)           # silent since the first
        snap, changes, dirty = exits.merge(snap, probed, ids, now=140)
        self.assertEqual((changes, dirty), ([], False))         # nothing new: the file is not rewritten
        self.assertEqual(snap["exits"][self.a2]["since"], 100)
        self.assertEqual(len(snap["exits"][self.a2]["tries"]), 5)            # 5 good checks of a2 in its window
        self.w.kill_leg(2)
        t = 150
        while True:                                             # a2 fails from now on: half of its window — dead
            snap, changes, dirty = exits.merge(snap, exits.probe(self.w.runner, self.pool), ids, now=t)
            changes = [c for c in changes if c[0] == self.a2]   # (VLESS: xray silent 60 s — dead at 160)
            if changes:
                break
            self.assertTrue(snap["exits"][self.a2]["alive"], t)
            t += 10
        self.assertEqual((changes, t), ([(self.a2, True, False)], 190))      # 5 failed of 10 checks
        self.assertEqual((snap["exits"][self.a2]["since"], snap["exits"][self.a2]["fail_since"]), (190, 150))
        self.assertNotIn("tries", snap["exits"][self.a2])                   # the window starts anew
        self.w.leg(2)
        snap, changes, _ = exits.merge(snap, exits.probe(self.w.runner, self.pool), ids, now=200)
        self.assertEqual(changes, [])                           # one good check: not back yet
        self.assertEqual(exits.state_of(snap["exits"][self.a2]), "dead")
        snap, changes, _ = exits.merge(snap, exits.probe(self.w.runner, self.pool), ids, now=210)
        self.assertEqual(changes, [(self.a2, False, True)])     # two in a row: alive again
        self.assertEqual(snap["exits"][self.a2]["fails"], 0)
        self.assertNotIn("fail_since", snap["exits"][self.a2])
        snap6, _, dirty = exits.merge(snap, {}, [self.a2], now=220)                  # the pool shrank
        self.assertTrue(dirty)
        self.assertEqual(list(snap6["exits"]), [self.a2])

    def test_one_failure_or_a_short_run_moves_nobody(self):
        """3.3.5: one lost probe, two in a row, 5 of the 12 checks of 2 min — alive; the words say the failed checks of the
        window; the sixth of 12 (half) — dead."""
        ok, bad = {"alive": True, "detail": "отвечает"}, {"alive": False, "detail": "нет ответа"}
        snap = {"exits": {}}
        script = [ok] * 6 + [bad] + [ok] * 3 + [bad, bad] + [ok] * 3 + [bad, bad] + [ok]
        for i, p in enumerate(script):
            snap, changes, _ = exits.merge(snap, {self.a2: p}, [self.a2], now=1000 + 10 * i)
            self.assertEqual(changes, [(self.a2, None, True)] if i == 0 else [], i)
            self.assertTrue(snap["exits"][self.a2]["alive"], i)
        snap, _, _ = exits.merge(snap, {self.a2: bad}, [self.a2], now=1000 + 10 * len(script))
        rec = snap["exits"][self.a2]
        self.assertEqual((rec["alive"], rec["fails"], rec["checks"]), (True, 5, 12))   # 5 of the 12 of 2 min
        self.assertIn("не прошло проверок: 5 из 12 за 2 мин", rec["detail"])
        snap, changes, _ = exits.merge(snap, {self.a2: bad}, [self.a2], now=1010 + 10 * len(script))
        self.assertEqual(changes, [(self.a2, True, False)])

    def test_a_vless_probe_read_twice_counts_once(self):
        """xray probes every 10–15 s, the collector reads every 5–10 s: the same last_try_time is one check."""
        bad = {"alive": False, "detail": "сервер выхода не отвечает", "try_at": 500}
        snap, _, _ = exits.merge({"exits": {}}, {self.vid: {"alive": True, "detail": "", "try_at": 400}},
                                 [self.vid], now=400)
        for t in range(410, 470, 5):                            # the same failed probe, read 12 times within a minute
            snap, changes, dirty = exits.merge(snap, {self.vid: bad}, [self.vid], now=t)
            self.assertEqual(changes, [])
        self.assertEqual((snap["exits"][self.vid]["alive"], len(snap["exits"][self.vid]["tries"])), (True, 2))
        t, n = 480, 501
        while True:                                             # new probes of xray, all failed
            snap, changes, _ = exits.merge(snap, {self.vid: dict(bad, try_at=n)}, [self.vid], now=t)
            if changes:
                break
            t, n = t + 12, n + 12
        self.assertEqual(changes, [(self.vid, True, False)])
        self.assertEqual(t, 492)                # 1 good and 3 different failed probes — not 14 reads of one

    def test_unknown_keeps_the_state_then_dead_since_the_silence_began(self):
        alive = {"alive": True, "detail": "xray: доступен"}
        unknown = {"alive": None, "detail": "xray шлюза не отвечает"}
        snap, changes, _ = exits.merge({"exits": {}}, {self.vid: alive}, [self.vid], now=0)
        self.assertEqual(changes, [(self.vid, None, True)])
        for t in (10, 20, 50):                                  # an xray restart: unknown, the exit stays
            snap, changes, dirty = exits.merge(snap, {self.vid: unknown}, [self.vid], now=t)
            self.assertEqual(changes, [])
            self.assertTrue(snap["exits"][self.vid]["alive"])
            self.assertEqual(dirty, t == 10)                    # only the start of the silence is written
        self.assertEqual(snap["exits"][self.vid]["unknown_since"], 10)
        # 3.3.5: silent for UNKNOWN_MAX s — dead at once, counted from the start of the silence (no window to wait)
        snap, changes, _ = exits.merge(snap, {self.vid: unknown}, [self.vid], now=10 + exits.UNKNOWN_MAX)
        self.assertEqual(changes, [(self.vid, True, False)])
        self.assertEqual(snap["exits"][self.vid]["fail_since"], 10)
        self.assertIn("(60 с)", snap["exits"][self.vid]["detail"])
        snap, changes, _ = exits.merge(snap, {self.vid: unknown}, [self.vid], now=90)
        self.assertEqual(changes, [])                           # told once
        snap, changes, _ = exits.merge(snap, {self.vid: alive}, [self.vid], now=200)
        self.assertEqual(changes, [])                           # one good check: not back yet
        snap, changes, _ = exits.merge(snap, {self.vid: alive}, [self.vid], now=210)
        self.assertEqual(changes, [(self.vid, False, True)])
        self.assertNotIn("unknown_since", snap["exits"][self.vid])

    def test_a_missing_leg_comes_up_alone_the_others_stay(self):
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual((res["healed"], res["ok"], res["missing"]), (True, True, ["kawg2", "kawg3"]))
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]])
        self.assertIn("from all fwmark 0x102 lookup 258", self.w.ip.rule_texts())
        self.assertEqual(self.w.ip.routes["258"], ["unreachable default metric 1000", "default dev kawg2 scope link"])
        idx2 = self.w.ip.links["kawg2"]["idx"]
        self.w.kill_leg(3)                                                # amneziawg-go of kawg3 died
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual((res["missing"], res["healed"], res["ok"]), (["kawg3"], True, True))
        self.assertEqual(self.w.ip.links["kawg2"]["idx"], idx2)           # kawg2 and its clients untouched
        self.assertEqual(self.w.script.brought_up, ["kawg2", "kawg3", "kawg3"])
        self.assertFalse(exits.ensure_legs(self.w.runner, self.pool)["healed"])          # nothing missing
        self.assertFalse(exits.ensure_legs(self.w.runner, [p for p in self.pool if p["kind"] == "vless"])["healed"])
        self.assertNotIn(["--egress-awg-up"], self.w.script.calls)        # never every leg at once
        self.assertIn("GW legs: ноги AmneziaWG в порядке, работающие не тронуты (kawg3 — не было интерфейса)",
                      self.read("/var/log/kaskad.log"))

    def test_a_starting_egress_does_not_hold_the_legs_back(self):
        # 3.2: the legs are units of their own (kaskad-leg@kawgN); the xray of the bot never brings them up or
        # down, so its start (or a crash loop of it) is no reason to leave a leg dead
        self.w.sysd.units["kaskad-egress.service"] = {"enabled": True, "active": False, "activating": True}
        res = exits.ensure_legs(self.w.runner, self.pool, force=True)
        self.assertEqual((res["healed"], res["ok"], res["missing"]), (True, True, ["kawg2", "kawg3"]))
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]])


class LegsHealTest(GwCase):
    """A restart of systemd-networkd takes the rules and the backstops of the legs (their device routes stay):
    the legs are repaired in place by --egress-awg-heal, never recreated for that."""

    def setUp(self):
        super().setUp()
        install_parser()
        write_pool([VLESS_A, awg_line(2), awg_line(3)])
        self.pool = exits.pool(self.w.runner)
        self.legs = [e for e in self.pool if e["kind"] == "awg"]
        self.w.leg(2)
        self.w.leg(3)

    def log(self):
        p = self.path("/var/log/kaskad.log")
        return self.read("/var/log/kaskad.log") if os.path.exists(p) else ""

    def test_whole_legs_cost_no_script(self):
        self.assertEqual(exits.leg_problems(self.w.runner, self.legs), {})
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual(res, {"missing": [], "broken": [], "healed": False, "ok": True})
        self.assertEqual(self.w.script.calls, [])

    def test_networkd_restart_is_healed_in_place(self):
        idx = dict((n, self.w.ip.links[n]["idx"]) for n in ("kawg2", "kawg3"))
        self.w.networkd_restart()
        self.assertEqual(exits.leg_problems(self.w.runner, self.legs),
                         {"kawg2": ["rule", "backstop"], "kawg3": ["rule", "backstop"]})
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual(res, {"missing": [], "broken": ["kawg2", "kawg3"], "healed": True, "ok": True})
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]])            # no --egress-awg-up
        self.assertEqual(dict((n, self.w.ip.links[n]["idx"]) for n in idx), idx)  # the interfaces stayed
        self.assertIn("from all fwmark 0x102 lookup 258", self.w.ip.rule_texts())
        self.assertIn("from all fwmark 0x103 lookup 259", self.w.ip.rule_texts())
        self.assertEqual(self.w.ip.routes["258"], ["unreachable default metric 1000", "default dev kawg2 scope link"])
        self.assertIn("GW legs: ноги AmneziaWG в порядке, работающие не тронуты (kawg2 — нет ip rule fwmark 0x102 "
                      "lookup 258, unreachable default table 258 metric 1000; kawg3", self.log())
        res = exits.ensure_legs(self.w.runner, self.pool)                         # whole again: nothing runs
        self.assertEqual((res["healed"], len(self.w.script.calls)), (False, 1))
        self.w.networkd_restart()                                                 # the next restart: at once,
        self.assertTrue(exits.ensure_legs(self.w.runner, self.pool)["healed"])    # a heal that helped never waits
        self.assertEqual(len(self.w.script.calls), 2)

    def test_each_part_is_seen(self):
        self.w.ip.rules = [r for r in self.w.ip.rules if "0x102" not in r[1]]
        self.w.ip.routes["259"].remove("default dev kawg3 scope link")
        self.assertEqual(exits.leg_problems(self.w.runner, self.legs), {"kawg2": ["rule"], "kawg3": ["dev"]})
        self.w.leg(2)
        self.w.leg(3)
        self.w.ip.routes["258"].remove("unreachable default metric 1000")
        self.assertEqual(exits.leg_problems(self.w.runner, self.legs), {"kawg2": ["backstop"]})
        self.w.ip.routes["258"] = []                       # an empty table: real ip says it does not exist
        self.w.runner._handlers.insert(0, (["ip", "-4", "route", "show", "table", "258"],
                                           (2, "", "Error: ipv4: FIB table does not exist.\nDump terminated\n")))
        self.assertEqual(exits.leg_problems(self.w.runner, self.legs), {"kawg2": ["dev", "backstop"]})

    def test_an_ip_that_does_not_answer_repairs_nothing(self):
        self.w.networkd_restart()
        self.w.runner._handlers.insert(0, (["ip", "-4", "rule", "show"], (1, "", "Cannot talk to rtnetlink\n")))
        self.w.runner._handlers.insert(0, (["ip", "-4", "route", "show"], (1, "", "Cannot talk to rtnetlink\n")))
        self.assertEqual(exits.leg_problems(self.w.runner, self.legs), {})
        self.assertFalse(exits.ensure_legs(self.w.runner, self.pool)["healed"])
        self.assertEqual(self.w.script.calls, [])

    def test_a_heal_that_did_not_help_waits_a_minute(self):
        self.w.networkd_restart()
        self.w.script.heal_rc = 2                                        # an old program: «Unknown option»
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual((res["healed"], res["ok"]), (True, False))
        self.assertIn("GW legs: gokaskad --egress-awg-heal (код 2) не привёл ноги AmneziaWG в порядок", self.log())
        res = exits.ensure_legs(self.w.runner, self.pool)                # within a minute: not again
        self.assertEqual((res["healed"], res["ok"], len(self.w.script.calls)), (False, False, 1))
        self.now += exits.HEAL_RETRY
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual((res["healed"], len(self.w.script.calls)), (True, 2))
        self.assertEqual(self.log().count("не привёл ноги"), 1)            # the first failure in a row only
        self.assertTrue(exits.ensure_legs(self.w.runner, self.pool, force=True)["healed"])   # gw apply: at once
        self.w.script.heal_rc = None                                     # the program is new again
        self.now += exits.HEAL_RETRY
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual((res["healed"], res["ok"]), (True, True))
        self.assertFalse(os.path.exists(exits._heal_stamp()))
        self.assertNotIn(["--egress-awg-up"], self.w.script.calls)

    def test_a_leg_that_cannot_come_up_leaves_the_others_alone(self):
        idx2 = self.w.ip.links["kawg2"]["idx"]
        self.w.kill_leg(3)
        self.w.script.fail_legs.add(3)                                   # its far end, its key — it does not come up
        for _ in range(3):
            res = exits.ensure_legs(self.w.runner, self.pool)
            self.assertEqual((res["missing"], res["healed"], res["ok"]), (["kawg3"], True, False))
            self.now += exits.HEAL_RETRY
        self.assertEqual(self.w.ip.links["kawg2"]["idx"], idx2)           # kawg2 never recreated
        self.assertIn("from all fwmark 0x103 lookup 259", self.w.ip.rule_texts())           # kawg3 closed:
        self.assertEqual(self.w.ip.routes["259"], ["unreachable default metric 1000"])     # no leak, no route

    def test_a_heal_that_says_done_never_loops(self):
        """rc 0 but the rules still look missing (say, a table named in rt_tables): a heal a minute, nothing more."""
        self.w.networkd_restart()
        self.w.script.heal_rc = 0
        for _ in range(3):
            res = exits.ensure_legs(self.w.runner, self.pool)
            self.assertEqual((res["healed"], res["ok"]), (True, False))
            self.now += exits.HEAL_RETRY
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]] * 3)

    def test_missing_and_broken_at_once(self):
        idx2 = self.w.ip.links["kawg2"]["idx"]
        self.w.networkd_restart()
        self.w.kill_leg(3)
        res = exits.ensure_legs(self.w.runner, self.pool)
        self.assertEqual((res["missing"], res["broken"], res["healed"], res["ok"]), (["kawg3"], ["kawg2"], True, True))
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]])
        self.assertEqual((self.w.ip.links["kawg2"]["idx"], self.w.script.brought_up), (idx2, ["kawg3"]))

    def test_repaired_while_the_egress_starts(self):
        self.w.networkd_restart()
        self.w.sysd.units["kaskad-egress.service"] = {"enabled": True, "active": False, "activating": True}
        res = exits.ensure_legs(self.w.runner, self.pool, force=True)
        self.assertEqual((res["broken"], res["healed"], res["ok"]), (["kawg2", "kawg3"], True, True))
        self.assertEqual(self.w.script.calls, [["--egress-awg-heal"]])


class MarksTest(GwCase):
    def test_rendezvous_moves_only_the_clients_of_a_dead_leg(self):
        legs = [{"id": "e-%010x" % i} for i in range(4)]
        keys = ["d-%08x" % i for i in range(400)]
        before = dict((k, exits.hrw(k, legs)["id"]) for k in keys)
        counts = dict((l["id"], sum(1 for v in before.values() if v == l["id"])) for l in legs)
        self.assertTrue(all(60 < c < 140 for c in counts.values()), counts)           # spread over the legs
        dead = legs[1]["id"]
        after = dict((k, exits.hrw(k, [l for l in legs if l["id"] != dead])["id"]) for k in keys)
        moved = [k for k in keys if before[k] != after[k]]
        self.assertEqual(sorted(moved), sorted(k for k in keys if before[k] == dead))
        self.assertIsNone(exits.hrw("d-1", []))

    def test_targets(self):
        """Stage 1 (SE §2.2): the exit of the device in the group of its route — its pin, else the rendezvous of 3.3.3
        among the fit exits (a VLESS exit by the device: 0x300+N, not the balancer 0x200); «exit:E» — E while fit, else
        another fit exit of its group; none — the last step of the group (its fail: block «fail-block» / direct);
        «block» / «direct» by choice. The form before stage 1 (pool, alive) — a decision with those exits alive."""
        st = self.scenario(save=False)
        pool = exits.pool(self.w.runner)
        alive = {self.vid, self.aid}
        d1, d2, d3 = st["gateway"]["devices"]
        self.assertEqual(exits.target(st, d1, pool, alive)["mark"], 0x102)        # pool: rendezvous
        self.assertEqual(exits.target(st, d2, pool, alive)["mark"], 0x102)        # user: exit:<awg>
        self.assertEqual(exits.target(st, d3, pool, alive)["mark"], 0x301)        # device: exit:<vless> (line 1)
        t = exits.target(st, d3, pool, {self.aid})
        self.assertEqual((t["mark"], t["exit"], t["step"]), (0x102, self.aid, "exit"))   # E down: its group (диф. 4)
        self.assertIn("недоступен", t["why"])
        t = exits.target(st, d2, pool, {self.vid})
        self.assertEqual((t["mark"], t["xray"]), (0x301, {"outboundTag": exits.tag(self.vid)}))
        self.assertEqual(exits.target(st, d1, pool, {self.vid})["mark"], 0x301)   # a VLESS exit by the device
        t = exits.target(st, d1, pool, set())
        self.assertEqual((t["mark"], t["step"], t["xray"]), (0x2ff, "last_block", {"outboundTag": "fail-block"}))
        t = exits.target(st, d3, pool, set())                                      # fail=direct
        self.assertEqual((t["mark"], t["exit"], t["step"]), (0x201, None, "last_direct"))
        self.assertIn("напрямую", t["why"])
        st["gateway"]["exits"][self.aid] = {"enabled": False}
        self.assertEqual(exits.target(st, d2, pool, alive)["mark"], 0x301)        # disabled for the gateway: the other
        self.assertEqual(exits.target(st, d1, pool, alive)["mark"], 0x301)
        d1["route"] = {"via": "direct", "fail": "block"}
        t = exits.target(st, d1, pool, alive)
        self.assertEqual((t["mark"], t["step"], t["xray"]), (0x201, "chosen", {"outboundTag": "direct"}))
        d1["route"] = {"via": "block", "fail": "block"}
        t = exits.target(st, d1, pool, alive)
        self.assertEqual((t["mark"], t["step"], t["xray"]), (0x2ff, "chosen", {"outboundTag": "block"}))

    def test_marks_skip_inactive_devices_and_take_leases(self):
        st = self.scenario(save=False)
        pool = exits.pool(self.w.runner)
        alive = {self.vid, self.aid}
        st["gateway"]["devices"][2]["enabled"] = False
        st["gateway"]["users"][1]["enabled"] = False                              # user of device 2
        dev1 = st["gateway"]["devices"][0]
        m = exits.marks(st, pool, alive, leases=[("10.77.8.10", dev1), ("10.77.8.11", st["gateway"]["devices"][2])])
        self.assertEqual(m, {"10.77.1.10": 0x102, "10.77.8.10": 0x102})

    def test_vless_exits_of_xray_and_the_table(self):
        st = self.scenario(save=False)
        pool = exits.pool(self.w.runner)
        self.assertEqual([(e["id"], e["mark"], exits.tproxy_port(e)) for e in exits.xray_vless(st, pool)],
                         [(self.vid, 0x301, 12401)])            # alive or not: the config does not follow liveness
        st["gateway"]["exits"][self.vid] = {"enabled": False}
        self.assertEqual(exits.xray_vless(st, pool), [])

    def test_direct_upstream_only_for_direct_routes(self):
        st = self.scenario(save=False)
        self.assertTrue(exits.direct_needed(st))                # d-3: exit:<vless> with fail=direct
        st["gateway"]["devices"][2]["route"] = {"via": "exit:" + self.vid, "fail": "block"}
        self.assertFalse(exits.direct_needed(st))
        model.set_route(st["gateway"]["devices"][0], "direct")
        self.assertTrue(exits.direct_needed(st))
        st["gateway"]["devices"][0]["enabled"] = False          # a disabled device needs nothing
        self.assertFalse(exits.direct_needed(st))
        st["gateway"]["devices"][0]["enabled"] = True
        st["gateway"]["devices"][0]["access"] = ["socks5"]      # SOCKS5 does not use dnsmasq
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        self.assertFalse(exits.direct_needed(st))
        model.set_route(st["gateway"]["users"][1], "block", "direct")    # fail of a «block» route means nothing
        self.assertFalse(exits.direct_needed(st))


class AddHintTest(GwCase):
    """3.2.21: where to add a key (the panel, the SSH menu with the number k3_gw_exits_menu really shows) and when the
    devices of the default route are without internet; the ids of the lines without the parser."""

    def test_menu_number_follows_the_default_route(self):
        st = {"gateway": {"enabled": True, "routes": {"default": {"via": "pool", "fail": "block"}}}}
        self.assertEqual((exits.menu_add_item(st), exits.add_hint(st)["menu"]), (3, "gokaskad → 17 → 3 → 3"))
        for via, n in (("exit:e-0123456789", 3), ("direct", 2), ("block", 2)):
            st["gateway"]["routes"]["default"] = {"via": via, "fail": "block"}
            self.assertEqual(exits.add_hint(st)["item"], n, via)
        hint = exits.add_hint({})                       # the gateway never set up: 17 opens the wizard
        self.assertEqual((hint["menu"], hint["item"]), ("gokaskad → 8 → 7 → 2", None))
        self.assertIn("«Выходы» → «Добавить ключ»", hint["text"])

    def test_install_sh_menu_order_is_what_the_number_says(self):
        """k3_gw_exits_menu: route first, «fail» only while the route goes through the exits, then «add»."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install.sh"),
                  encoding="utf-8") as f:
            body = f.read().split("\nk3_gw_exits_menu() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('acts=(route); [ -n "$viaexit" ] && acts+=(fail); acts+=(add)', body)
        self.assertIn('viaexit=""; case "$via" in pool|exit:*|group:*) viaexit=1 ;; esac', body)   # T10: a group too

    def test_offline_only_when_empty_on_and_blocking(self):
        write_pool([])
        st = {"gateway": {"enabled": True, "routes": {"default": {"via": "pool", "fail": "block"}}}}
        self.assertTrue(exits.offline_empty(st))
        self.assertFalse(exits.offline_empty(st, 1))
        st["gateway"]["routes"]["default"]["fail"] = "direct"
        self.assertFalse(exits.offline_empty(st))
        st["gateway"]["routes"]["default"] = {"via": "exit:e-0123456789", "fail": "block"}
        self.assertTrue(exits.offline_empty(st))
        st["gateway"]["routes"]["default"] = {"via": "direct", "fail": "block"}
        self.assertFalse(exits.offline_empty(st))
        st["gateway"] = {"enabled": False}
        self.assertFalse(exits.offline_empty(st))

    def test_line_ids_without_the_parser(self):
        write_pool([VLESS_A, "", VLESS_B, VLESS_A])
        a, b = exits.exit_id(VLESS_A), exits.exit_id(VLESS_B)
        self.assertEqual(exits.line_ids(), [(a, 1), (b, 2), (a + "-3", 3)])


def conf_of(line, n=1, endpoint="198.51.100.31:51820"):
    """A fake .conf of a line: 1.0 / 3.1 (fakes_gw.awg_conf), 1.5 (+ I1), 2.0 (+ S3, S4), «wg» (plain WireGuard)."""
    from tests.fakes_gw import awg_conf
    if line == "wg":
        keep = ("[Interface]", "Address", "DNS", "PrivateKey", "", "[Peer]", "PublicKey", "PresharedKey", "AllowedIPs",
                "Endpoint", "PersistentKeepalive")
        return "\n".join(ln for ln in awg_conf(n, endpoint=endpoint).splitlines()
                         if ln.split(" =")[0] in keep) + "\n"
    text = awg_conf(n, endpoint=endpoint, line="3.1" if line == "3.1" else "1.0")
    extra = {"1.5": "I1 = <b 0xf6ab3267fa><c><b 0xf6ab><t><r 10><wt 10>\n", "2.0": "S3 = 20\nS4 = 12\n"}.get(line, "")
    return text.replace("[Peer]", extra + "\n[Peer]") if extra else text


def vpn_of(conf, name="Офис", container="awg"):
    """A fake vpn:// of the Amnezia app: 4 bytes of length + zlib(JSON), base64url."""
    import base64
    import struct
    import zlib
    doc = {"description": name, "containers": [{"container": "amnezia-" + container,
                                                 container: {"last_config": json.dumps({"config": conf})}}]}
    raw = json.dumps(doc).encode("utf-8")
    return "vpn://" + base64.urlsafe_b64encode(struct.pack(">I", len(raw)) + zlib.compress(raw)).decode().rstrip("=")


class PreviewTest(GwCase):
    """3.2.21-P2: what a pasted text or a file holds, by its content (not its name) — the menu's parser in-process."""

    def setUp(self):
        super().setUp()
        install_parser()
        self.parser = exits.load_parser()

    def titles(self, text, pool=()):
        return [(x["title"], x["where"], x["in_pool"]) for x in exits.preview(self.parser, text, pool)]

    def test_lines_of_amneziawg_and_wireguard_by_content(self):
        for line, title in (("1.0", "AmneziaWG 1.0"), ("1.5", "AmneziaWG 1.5"), ("2.0", "AmneziaWG 2.0"),
                            ("3.1", "AmneziaWG 3.1"), ("wg", "WireGuard")):
            self.assertEqual(self.titles(conf_of(line)), [(title, "198.51.100.31:51820", False)], line)
        self.assertEqual(self.titles(vpn_of(conf_of("2.0", 7), "Офис")), [("AmneziaWG 2.0", "198.51.100.31:51820", False)])
        self.assertEqual(exits.preview(self.parser, vpn_of(conf_of("2.0", 7), "Офис"))[0]["name"], "Офис")
        text = "мои ключи:\n%s\n\n%s\n" % (VLESS_A, VLESS_B)                 # a .txt with links
        self.assertEqual(self.titles(text, {exits.exit_id(VLESS_A)}),
                         [("VLESS", "198.51.100.20:443", True), ("VLESS", "198.51.100.21:8443", False)])

    def test_the_parser_and_awgkeys_tell_the_same_line(self):
        """One set of rules: egress_parse.awg_version (the pool, the preview) and vendor/awgkeys.detect_line."""
        from kaskad.vendor import awgkeys
        for line in ("1.0", "1.5", "2.0", "3.1", "wg"):
            (key,) = self.parser.extract(conf_of(line))
            rec = exits._awg_record(self.parser, key[6:])
            self.assertEqual(self.parser.awg_version(rec), awgkeys.detect_line(rec["params"]), line)

    def test_a_wireguard_key_is_a_working_leg(self):
        """A plain WireGuard .conf: an AmneziaWG record with no parameters — the leg's config is plain WireGuard."""
        (line,) = self.parser.extract(conf_of("wg"))
        rec = exits._awg_record(self.parser, line[6:])
        self.assertTrue(exits.plain_wg(rec))
        conf = self.parser.awg_setconf(rec)
        self.assertNotRegex(conf, r"(?m)^(Jc|Jmin|Jmax|S\d|H\d|I\d) =")
        self.assertRegex(conf, r"(?m)^PrivateKey = \S+$")
        self.assertRegex(conf, r"(?m)^Endpoint = 198\.51\.100\.31:51820$")
        write_pool([line])
        (e,) = exits.pool(self.w.runner)
        self.assertEqual((e["kind"], e["iface"], exits.kind_title(e), exits.public(e)["wg"]), ("awg", "kawg1", "WireGuard", True))

    def test_nothing_secret_and_honest_refusals(self):
        out = json.dumps([exits.preview(self.parser, t) for t in (conf_of("3.1"), vpn_of(conf_of("1.0")), VLESS_A)],
                         ensure_ascii=False)
        for secret in ("PrivateKey", "11111111-2222", "vpn://", "awg://", "vless://", "pbk", "HeaderProtectionKey"):
            self.assertNotIn(secret, out)
        with self.assertRaises(KaskadError) as c:
            exits.preview(self.parser, "просто текст")
        self.assertEqual(str(c.exception), exits.NO_KEYS_TEXT)
        with self.assertRaises(KaskadError) as c:
            exits.preview(self.parser, vpn_of(conf_of("wg"), container="wireguard"))
        self.assertEqual(str(c.exception), exits.NO_AWG_IN_VPN)
        self.assertTrue(exits.is_subscription("https://example.org/sub/abc\n"))
        self.assertFalse(exits.is_subscription(conf_of("1.0")))

    def test_overlapping_previews_give_stderr_back(self):
        """Critic 3.2.21: redirect_stderr of two threads at once left sys.stderr on a StringIO for good. One lock."""
        import sys
        import threading
        import time
        real = self.parser

        class Slow(object):
            def __getattr__(self, name):
                return getattr(real, name)

            def extract(self, text):
                time.sleep(0.2)
                return real.extract(text)
        was = sys.stderr
        ts = [threading.Thread(target=exits.preview, args=(Slow(), VLESS_A)) for _ in range(2)]
        ts[0].start()
        time.sleep(0.05)
        ts[1].start()
        for t in ts:
            t.join()
        self.assertIs(sys.stderr, was)

    def test_the_text_of_a_key_is_checked_first(self):
        from kaskad.gw import ops
        for bad, why in (("", "Вставьте ключ"), ("x" * (64 * 1024 + 1), "длиннее 64 КБ"), ("[Interface]\x00\x01", ops.NOT_TEXT),
                         ("�" * 20 + VLESS_A, ops.NOT_TEXT), (paths_file(self), ops.PATH_TEXT)):
            with self.assertRaises(KaskadError) as c:
                ops.key_text(bad)
            self.assertIn(why, str(c.exception))
        self.assertEqual(ops.key_text(VLESS_A + "\n"), VLESS_A + "\n")
        self.assertEqual(ops.key_text("[Interface]" + " " * 300 + "PrivateKey = x\n" + " \t\n" * 40 + "y"),
                         "[Interface] PrivateKey = x\ny")                         # long runs of blanks squeezed
        self.assertEqual(ops.exit_preview(self.w.runner, "https://example.org/sub\n")["text"], exits.SUBSCRIPTION_TEXT)


def paths_file(case):
    from kaskad import paths
    return " %s \n" % paths.egress_parser()
