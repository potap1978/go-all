"""Stage 1, T4 (SPEC-stage1-exits §2.2–2.7, §3.1, §3.2, §3.6, §4.2, §4.4, §4.6, §5.1, §5.6, §8.2; §11.1 «collect.py» and
«Слой событий»): the tick of the collector, the decision of other processes, the layer of the events."""

import json
import os
from unittest import mock

from kaskad import i18n, paths, state
from kaskad.errors import KaskadError, NestedLockError
from kaskad.gw import collect, exits, live, model, net, pins, policy as P, rev, ruguard, xray
from tests.helpers import legacy_state
from tests.test_gw_ops import CliGwCase


class S1World(CliGwCase):
    """A gateway on: VLESS A (line 1) + AmneziaWG (line 2), a device «A» of «Мама» on WireGuard."""

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(live, "_now", lambda: self.now), mock.patch.object(collect, "LIVE", live.Live()),
                  mock.patch.object(live, "_mono", lambda: self.now)):
            p.start()
            self.addCleanup(p.stop)
        self.enabled_world()
        self.ok("gw", "inbound", "install", "wg")
        self.ok("gw", "user", "add", "Мама")
        self.ok("gw", "device", "add", "Мама", "A", "--access", "wg")
        self.w.sysd.units[xray.UNIT]["active"] = True
        self.w.curl.observatory = {"gx-" + self.vid: True}

    def round(self, dt=10):
        self.now += dt
        return collect.tick(self.w.runner)

    def dev(self):
        return model.devices(state.load())[0]

    def events(self):
        return json.loads(self.read("/run/kaskad/live.json"))["events"]


class TickTest(S1World):
    def test_nested_lock_is_an_error_in_the_journal_not_a_skipped_round(self):
        """Проверка 19, придирка 4: NestedLockError — a bug of the code: a line «ОШИБКА» with the place, not «skipped»."""
        class Nested(object):
            def __enter__(self):
                raise NestedLockError("nested")

            def __exit__(self, *a):
                return False
        with mock.patch.object(state, "locked", lambda timeout=None: Nested()):
            res = self.round()
        self.assertNotIn("skipped", res)
        self.assertEqual(res["error"], "nested")
        self.assertIn("ОШИБКА — вложенная блокировка", self.log())
        self.assertIn("collect.py", self.log())

    def test_pins_and_the_snapshot_are_written_in_the_tick_under_the_lock(self):
        for _ in range(3):
            self.round()
        doc = pins.read()
        d = self.dev()
        pin = doc["groups"]["main"]["pins"][d["id"]]
        self.assertIn(pin["exit"], (self.vid, self.aid))
        snap = exits.load_snapshot()
        self.assertEqual(snap["exits"][self.aid]["state"], "ok")
        self.assertIn("state_since", snap["exits"][self.aid])
        # another process reads the same decision from the files (§2.2)
        t = exits.target(state.load(), d)
        self.assertEqual((t["exit"], t["step"], t["group"]), (pin["exit"], "exit", "main"))
        want = 0x300 + 1 if pin["exit"] == self.vid else 0x102                      # a VLESS exit by the device
        self.assertEqual(self.w.nft.elements("gw_route"), {"10.77.1.10": want})

    def test_update_map_gets_the_decision_of_the_tick_and_xray_runs_after_the_lock(self):
        seen = {}
        real = net.update_map

        def um(runner, st, live=None, elems=None, decision=None, ports=False):
            seen["decision"] = decision
            seen.setdefault("ports", []).append(ports)
            return real(runner, st, live=live, elems=elems, decision=decision, ports=ports)

        def xa(st, sec, runner, pool, **kw):
            try:                                     # the state lock is not held by this thread any more (§2.5)
                with state.locked(timeout=0.2):
                    seen["free"] = True
            except NestedLockError:
                seen["free"] = False
            seen["kw"] = kw
            seen["tick"] = collect.MEM.decision
            return {}
        with mock.patch.object(net, "update_map", um), mock.patch.object(xray, "apply", xa):
            self.round()
            self.round()
        self.assertIs(seen["decision"], collect.MEM.decision)
        self.assertTrue(seen["free"])
        self.assertIs(seen["kw"]["decision"], seen["tick"])
        self.assertTrue(seen["kw"]["skip_rejected"])
        self.assertEqual(seen["kw"]["lock_wait"], collect.XRAY_LOCK_WAIT)

    def test_a_refused_config_is_told_once(self):
        """§2.7: ``xray_rejected`` once (the same refusal again at most hourly), the journal says it."""
        def xa(*a, **kw):
            raise KaskadError("xray: bad config")
        with mock.patch.object(xray, "apply", xa), mock.patch.object(xray, "render_wanted",
                                                                           lambda fp: True, create=True):
            for _ in range(4):
                self.round()
        kinds = [e["kind"] for e in self.events() if e["kind"] == "xray_rejected"]
        self.assertEqual(kinds, ["xray_rejected"])
        self.assertIn("xray отверг конфиг сборщика", self.log())

    def test_bo_gw_all_from_the_decision(self):
        calls = []
        self.w.sysd.units[xray.UNIT]["since_mono"] = 5.0
        with mock.patch.object(xray, "balancer_override", lambda runner, tgt, bal="gw-all", *a, **kw:
                               calls.append((bal, tgt)) or True, create=True), \
                mock.patch.object(xray, "balancer_info", lambda *a, **kw: None, create=True):
            for _ in range(3):
                self.round()
        self.assertTrue(calls)
        self.assertEqual(calls[-1], ("gw-all", collect.MEM.decision["gw_all"]))
        self.assertTrue(collect.MEM.decision["gw_all"].startswith("gx-"))
        n = len(calls)
        self.round()
        self.assertEqual(len(calls), n)                          # the same target, the same xray: not again
        self.w.sysd.units[xray.UNIT]["since_mono"] = 7.0            # xray started again: the override is lost
        with mock.patch.object(xray, "balancer_override", lambda runner, tgt, bal="gw-all", *a, **kw:
                               calls.append((bal, tgt)) or True, create=True):
            self.round()
        self.assertEqual(len(calls), n + 1)

    def test_a_new_exit_is_placed_and_counted_once(self):
        """Проверка 16: the collector places a new line of the pool into its group — ``rev`` + 1 (``rev.note`` under the
        lock of the tick); the next tick changes nothing."""
        st = state.load()
        doc = P.default_doc("compat")
        doc["groups"][0]["tiers"][0]["exits"] = [self.vid]       # the AmneziaWG line is not placed yet
        st["gateway"]["exit_policy"] = doc
        state.save(st)
        before = rev.rev_of(state.load())
        self.round()
        st = state.load()
        self.assertIn(self.aid, P.exits_of(P.group(P.stored(st), "main")))
        self.assertEqual(rev.rev_of(st), before + 1)
        self.round()
        self.assertEqual(rev.rev_of(state.load()), before + 1)


class NodeNetTest(S1World):
    def test_the_network_of_the_node_before_the_states(self):
        """§4.6: every exit silent and the direct probe silent — the node is offline: states frozen, ``node_offline``,
        live.json policy.node_offline; back — ``node_online`` FIRST in its tick (З2), with policy false in the same write."""
        for _ in range(3):
            self.round()
        self.w.leg(2, probe=False)
        self.w.curl.observatory = {"gx-" + self.vid: False}
        self.w.net_up = False
        for _ in range(12):
            self.round(5)
        snap = exits.load_snapshot()
        self.assertEqual(snap["exits"][self.aid]["state"], "ok")              # frozen, not «dead»
        doc = json.loads(self.read("/run/kaskad/live.json"))
        self.assertTrue(doc["policy"]["node_offline"])
        self.assertFalse(doc["policy"]["node_ru_offline"])                    # T15 Q6: the Russian side apart
        self.assertEqual(sorted(doc["groups"]), ["main"])                      # Q6: live.groups of the decision
        # PLAN-order Ш2: и ряды «силы» уровней — у замороженного узла тоже (по сохранённым замерам)
        self.assertEqual(sorted(doc["groups"]["main"]), ["active_tier", "last_step", "ranks", "since"])
        self.assertIn("node_offline", [e["kind"] for e in doc["events"]])
        self.w.net_up = True
        self.w.leg(2)
        self.w.curl.observatory = {"gx-" + self.vid: True}
        self.round()
        doc = json.loads(self.read("/run/kaskad/live.json"))
        self.assertFalse(doc["policy"]["node_offline"])
        kinds = [e["kind"] for e in doc["events"]]
        self.assertIn("node_online", kinds)

    def _offline(self, n=12):
        self.w.leg(2, probe=False)
        self.w.curl.observatory = {"gx-" + self.vid: False}
        self.w.net_up = False
        for _ in range(n):
            self.round(5)

    def _online(self):
        self.w.net_up = True
        self.w.leg(2)
        self.w.curl.observatory = {"gx-" + self.vid: True}

    def test_a_restart_while_offline_closes_the_outage_when_the_network_is_there(self):
        """REV-s1-t4 З1: the collector restarted while offline, the network back before its first round — the outage
        is closed by ``node_online`` (the end — the last round of the previous process, «после перезапуска»); the next
        real outage has its own ``node_offline``."""
        for _ in range(3):
            self.round()
        self._offline()
        last = json.loads(self.read("/run/kaskad/live.json"))["ts"]
        since = [e for e in self.events() if e["kind"] == "node_offline"][0]["args"]["since"]
        collect.MEM.reset()                                   # a restart of the collector process
        collect.LIVE = live.Live()
        self._online()
        self.now += 3600
        self.round()
        doc = json.loads(self.read("/run/kaskad/live.json"))
        self.assertFalse(doc["policy"]["node_offline"])
        on = [e for e in doc["events"] if e["kind"] == "node_online"]
        self.assertEqual(len(on), 1)
        self.assertEqual(on[0]["args"]["since"], since)
        self.assertEqual(on[0]["args"]["until"], int(last))
        self.assertTrue(on[0]["args"]["restart"])
        self.assertIn("после перезапуска", on[0]["text"])
        for _ in range(3):
            self.round()
        self._offline(4)                                      # a new outage of a minute: told as its own
        self._online()
        self.round()
        kinds = [e["kind"] for e in self.events() if e["kind"] in ("node_offline", "node_online")]
        self.assertEqual(kinds, ["node_offline", "node_online", "node_offline", "node_online"])
        last_on = [e for e in self.events() if e["kind"] == "node_online"][-1]
        self.assertNotIn("restart", last_on["args"])
        self.assertLess(last_on["args"]["minutes"], 5)

    def test_the_windows_start_anew_when_the_network_is_back(self):
        """§4.6, T2 З5/П2 (REV-s1-t4 З5): the tick that tells the network back starts the windows of the exits anew
        (``health.node_online`` before ``evaluate``): no check from before the outage stays in «упал»."""
        for _ in range(4):
            self.round()
        before = set(tuple(t) for t in collect.MEM.health[self.aid]["tries"])
        self.assertTrue(before)
        self._offline()
        self.assertEqual(set(tuple(t) for t in collect.MEM.health[self.aid]["tries"]), before)   # frozen
        self._online()
        self.round()
        self.assertIn("node_online", [e["kind"] for e in self.events()])
        after = set(tuple(t) for t in collect.MEM.health[self.aid]["tries"])
        self.assertFalse(before & after, (before, after))
        self.assertLessEqual(len(after), 1)

    def test_a_restart_while_still_offline_keeps_the_outage_and_ends_it_when_seen(self):
        """REV-s1-t4 З1: restarted while offline and still offline — one outage, its end the round the network is back
        (no «после перезапуска»)."""
        for _ in range(3):
            self.round()
        self._offline()
        collect.MEM.reset()
        collect.LIVE = live.Live()
        self.round(5)
        doc = json.loads(self.read("/run/kaskad/live.json"))
        self.assertTrue(doc["policy"]["node_offline"])
        self.round(600)
        self._online()
        self.round()
        on = [e for e in self.events() if e["kind"] == "node_online"]
        self.assertEqual(len(on), 1)
        self.assertNotIn("restart", on[0]["args"])
        self.assertEqual(on[0]["args"]["until"], int(self.now))
        self.assertEqual([e["kind"] for e in self.events()].count("node_offline"), 1)

    def test_no_probe_no_part_in_all_silent(self):
        """С2: an exit off by its quota and the good exits of an idle lazy group are not «silent» — no direct probe."""
        pool = [{"id": "e-00000000a1", "kind": "awg", "mark": 257, "role": "abroad"},
                {"id": "e-00000000a2", "kind": "awg", "mark": 258, "role": "abroad"}]
        pol = P.default_doc("compat")
        pol["groups"][0]["tiers"][0]["exits"] = ["e-00000000a1", "e-00000000a2"]
        probed = {"e-00000000a1": {"alive": False}, "e-00000000a2": {"alive": False}}
        called = []
        with mock.patch.object(exits, "direct_probe", lambda *a, **kw: called.append(1) or False):
            got = collect.node_check(pool, probed, pol, {"off": {"e-00000000a1"}, "idle": {"e-00000000a2"}})
            self.assertFalse(got["checked"] or called)
            got = collect.node_check(pool, probed, pol, {"off": set(), "idle": set()})
        self.assertTrue(got["offline"] and called)


class ProbePlanTest(S1World):
    def test_the_period_of_the_group(self):
        """§3.2: AmneziaWG once in P of its group («Обычно — 30 с»), not every round; xray every round."""
        st = state.load()
        pol = P.load(st, exits.pool(self.w.runner))
        P.group(pol, "main")["checks"]["freq"] = {"mode": "normal", "every_s": None}
        pool = exits.pool(self.w.runner)
        collect.MEM.health = {}
        p1 = collect.probe_plan(st, pol, pool, 1000.0)
        self.assertIn(self.aid, p1["only"])
        collect.MEM.awg_at[self.aid] = 1000.0
        self.assertNotIn(self.aid, collect.probe_plan(st, pol, pool, 1010.0)["only"])
        self.assertIn(self.vid, collect.probe_plan(st, pol, pool, 1010.0)["only"])
        self.assertIn(self.aid, collect.probe_plan(st, pol, pool, 1028.0)["only"])

    def test_lazy_and_quota(self):
        st = state.load()
        pool = exits.pool(self.w.runner)
        pol = P.load(st, pool)
        P.group(pol, "main")["checks"]["freq"] = {"mode": "lazy", "every_s": None}
        collect.MEM.health = {self.aid: {"state": "ok"}, self.vid: {"state": "off", "reasons": ["quota"],
                                                                     "quota": {"action": "remove"}}}
        collect.MEM.awg_at[self.aid] = 4900.0                       # checked 100 s ago (T-q Н-lazy: the wake is 300 s)
        p = collect.probe_plan(st, pol, pool, 5000.0)
        self.assertEqual((p["only"], p["idle"], p["off"]), (set(), {self.aid}, {self.vid}))
        collect.MEM.busy_at[self.aid] = 4800.0                     # traffic within 10 min: probed
        self.assertIn(self.aid, collect.probe_plan(st, pol, pool, 5000.0)["only"])
        collect.MEM.health[self.aid] = {"state": "standby"}       # not good: always (every 15 s)
        collect.MEM.busy_at.clear()
        self.assertIn(self.aid, collect.probe_plan(st, pol, pool, 5000.0)["only"])


class BotTest(S1World):
    def test_bo_of_the_bot(self):
        """§2.9: ``bo bot-bal out_<N>`` — the best fit exit of the group of the proxy; ``.override`` wins; none fit —
        ``-r``; a restart of kaskad-egress — again."""
        self.write("/etc/kaskad/egress/xray", "")
        self.w.sysd.units[exits.EGRESS_UNIT] = {"enabled": True, "active": True, "since_mono": 5.0}
        calls = []
        real = self.w.runner.run

        def run(argv, **kw):
            if argv[1:3] == ["api", "bo"]:
                calls.append(argv[-1])
                return type("R", (), {"ok": True, "out": "", "err": "", "rc": 0})()
            return real(argv, **kw)
        pool = exits.pool(self.w.runner)
        st = state.load()
        d = {"states": {self.vid: "ok", self.aid: "degraded"}}
        with mock.patch.object(self.w.runner, "run", run):
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(calls, ["out_1"])                   # good before degraded
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(calls, ["out_1"])                   # the same: not again
            d["states"][self.vid] = "off"
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(calls[-1], "out_2")
            d["states"][self.aid] = "off"
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(calls[-1], "-r")
            self.write("/etc/kaskad/egress/.override", "2\n")
            n = len(calls)
            d["states"][self.vid] = "ok"
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(len(calls), n)                      # the owner's choice in the menu wins
            os.remove(self.path("/etc/kaskad/egress/.override"))
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(calls[-1], "out_1")
            self.w.sysd.units[exits.EGRESS_UNIT]["since_mono"] = 9.0     # restarted: the target is lost
            collect._bot(self.w.runner, st, d, pool)
            self.assertEqual(calls[-2:], ["out_1", "out_1"])


class DecisionTest(S1World):
    def test_exit_x_gone_from_the_pool_goes_through_its_group(self):
        """§2.2, З1: «exit:X», X not in the pool — the group where X is still listed (else «main»), ``missing``."""
        st = state.load()
        d = self.dev()
        gone = "e-0000000fff"
        d["route"] = {"via": "exit:" + gone, "fail": "block"}
        pool = exits.pool(self.w.runner)
        t = exits.target(st, d, pool, {self.vid, self.aid})
        self.assertTrue(t["missing"])
        self.assertEqual((t["group"], t["step"]), ("main", "exit"))
        self.assertIn(t["exit"], (self.vid, self.aid))

    def test_direct_needed_by_the_group(self):
        """§2.6, С4: the group of the route with ``fail = direct`` needs the direct upstream of dnsmasq (static)."""
        st = state.load()
        self.assertFalse(exits.direct_needed(st))
        doc = P.default_doc("compat")
        doc["groups"][0]["fail"] = "direct"
        st["gateway"]["exit_policy"] = doc
        self.assertTrue(exits.direct_needed(st))
        self.assertTrue(exits.direct_needed(st, P.load(st)))
        model.set_route(model.devices(st)[0], "block")
        self.assertFalse(exits.direct_needed(st))              # «block» by choice: no direct

    def test_ruonly_only_for_the_fallback_block(self):
        """§2.3, Б1: gw_ruonly — gw_rd addresses on the LAST STEP «block» of their group; never a block by choice."""
        tg = {"10.77.1.10": {"mark": exits.MARK_BLOCK, "step": "last_block"},
              "10.77.1.11": {"mark": exits.MARK_BLOCK, "step": "chosen"},
              "10.77.1.12": {"mark": 0x102, "step": "exit"}}
        self.assertEqual(net.ruonly_of(tg, ["10.77.1.10", "10.77.1.11", "10.77.1.12"]), ["10.77.1.10"])
        st = state.load()
        with mock.patch.object(ruguard, "allowed", return_value=True):     # D-136: gw_ruonly needs «Россия напрямую»
            self.assertEqual(net.live_ips(st, {"10.77.1.10": exits.MARK_BLOCK, "10.77.1.12": 0x102}, ["10.77.1.10"]),
                             ["10.77.1.10", "10.77.1.12"])
        self.assertEqual(net.live_ips(st, {"10.77.1.10": exits.MARK_BLOCK, "10.77.1.12": 0x102}, ["10.77.1.10"]),
                         ["10.77.1.12"])                                    # without the consent ⚠РФ: not live
        self.assertEqual(net.live_ips(st, {"10.77.1.10": exits.MARK_BLOCK}), [])

    def test_group_route_is_one_path(self):
        """REV-s1-fix1: «group:<gid>» — its group in ``target`` / ``now_of`` (kernel and xray alike), not the pool."""
        st = state.load()
        doc = P.default_doc("compat")
        doc["groups"][0]["tiers"][0]["exits"] = [self.vid]
        g = P.default_group("g-abc123", "abroad", "compat", name="Стриминг", exits=[self.aid])
        doc["groups"].append(g)
        st["gateway"]["exit_policy"] = doc
        d = self.dev()
        d["route"] = {"via": "group:g-abc123", "fail": "block"}
        pool = exits.pool(self.w.runner)
        dec = exits.decision(st, pool_exits=pool, alive={self.vid, self.aid})
        t = exits.target(st, d, dec)
        self.assertEqual((t["group"], t["exit"], t["mark"]), ("g-abc123", self.aid, 0x102))
        for socks in (False, True):
            n = exits.now_of(st, d, pool, socks=socks, decision=dec)
            self.assertEqual((n["group"], n["exit"], n["target"]), ("g-abc123", self.aid, "exit:" + self.aid))
        dec = exits.decision(st, pool_exits=pool, alive={self.vid})
        t = exits.target(st, d, dec)                             # its group has nothing fit: its last step
        self.assertEqual((t["step"], t["xray"]), ("last_block", {"outboundTag": "fail-block"}))


class LabelTest(S1World):
    def test_group_names_by_the_label(self):
        """§3.1, проверка 16/17: ``name`` of ``group_last_step`` and ``dns_via`` — ``policy.label`` («Основная»/«Main»)."""
        L = live.Live()
        L._loaded = True
        pol = P.default_doc("compat")
        labels = live._group_labels({"policy": pol}, None)
        L.publish([{"kind": "group_last_step", "group": "main", "role": "abroad", "step": "block", "devices": 2,
                    "dns_via": "none"}], {}, labels, {"exits": {}}, 1000)
        ev = L.events[-1]
        self.assertEqual(ev["args"]["name"], "Основная")
        self.assertIn("Основная", live.event_text(ev, "ru"))
        with i18n.use_lang("en"):
            self.assertEqual(live._group_labels({"policy": pol}, None)["main"], "Main")


class FeedTest(S1World):
    """§3.6, §8.2: the antispam of 3.3.5 for the new kinds and the groups; manual — at once; windows monotonic."""

    def setUp(self):
        super().setUp()
        self.L = live.Live()
        self.L._loaded = True
        self.mono = [100000.0]
        p = mock.patch.object(live, "_mono", lambda: self.mono[0])
        p.start()
        self.addCleanup(p.stop)
        self.wall = [1790000000.0]
        p = mock.patch.object(live, "_now", lambda: self.wall[0])
        p.start()
        self.addCleanup(p.stop)

    def at(self, minutes):
        self.mono[0] = 100000.0 + minutes * 60
        self.wall[0] = 1790000000.0 + minutes * 60
        return self.wall[0]

    def pub(self, minutes, kind, snap=None, **kw):
        now = self.at(minutes)
        ev = dict({"kind": kind, "exit": "e-1", "group": "main", "role": "abroad"}, **kw)
        self.L.publish([ev], {"e-1": "X"}, {"main": "Основная"}, snap or {"exits": {}}, now)

    def kinds(self):
        return [e["kind"] for e in self.L.events]

    def test_another_kind_at_once_repeats_held(self):
        self.pub(0, "exit_dead")
        self.pub(1, "exit_alive")
        self.pub(5, "exit_dead")
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive"])
        self.pub(6, "exit_standby", why="rule:r1")                 # another kind: at once
        self.assertEqual(self.kinds()[-1], "exit_standby")

    def test_window_end_tells_the_state(self):
        dead = {"exits": {"e-1": {"state": "off", "reasons": ["dead"]}}}
        self.pub(0, "exit_dead", dead)
        self.pub(1, "exit_alive")
        self.pub(5, "exit_dead", dead)
        self.at(16)
        self.L._window_end({"e-1": "X"}, dead, self.wall[0])
        self.assertEqual(self.kinds(), ["exit_dead", "exit_alive", "exit_dead"])

    def test_group_last_step_then_restored_at_once(self):
        self.L.publish([{"kind": "group_last_step", "group": "main", "step": "block", "devices": 1}], {}, {"main": "M"},
                       {"exits": {}}, self.at(0))
        self.L.publish([{"kind": "group_restored", "group": "main", "exit": "e-1"}], {"e-1": "X"}, {"main": "M"},
                       {"exits": {}}, self.at(2))
        self.assertEqual(self.kinds(), ["group_last_step", "group_restored"])

    def test_manual_goes_at_once(self):
        self.pub(0, "exit_standby", why="unstable", flaps=3)
        self.pub(5, "exit_standby", why="manual")
        self.assertEqual(self.kinds(), ["exit_standby", "exit_standby"])
        self.pub(6, "exit_recovered", **{"from": "standby"})     # «Вернуть» after a manual state: at once
        self.assertEqual(self.kinds()[-1], "exit_recovered")

    def test_flap_summary_by_falls(self):
        """С1-5 / З1-5: in the end of the window, with ``flap_summary``, ≥ 3 falls of health — «нестабилен» with
        ``flaps``; an exit «в запас» as unstable — nothing more."""
        ok = {"exits": {"e-1": {"state": "ok", "reasons": [], "falls": []}}}
        self.pub(0, "exit_dead")
        self.pub(1, "exit_alive")
        self.pub(3, "exit_dead")
        self.pub(4, "exit_alive")
        now = self.at(16)
        ok["exits"]["e-1"]["falls"] = [now - 600, now - 500, now - 400]
        self.L._window_end({"e-1": "X"}, ok, now, None, live._flaps(ok, now))
        self.assertEqual(self.L.events[-1]["args"].get("flaps"), 3)
        self.assertIn("нестабилен", self.L.events[-1]["text"])

    def test_windows_on_the_monotonic_clock(self):
        self.pub(0, "exit_dead")
        self.mono[0] += 60
        self.wall[0] += 7200                                      # the wall clock jumped 2 h forward
        self.L.publish([{"kind": "exit_dead", "exit": "e-1"}], {"e-1": "X"}, {}, {"exits": {}}, self.wall[0])
        self.assertEqual(self.kinds(), ["exit_dead"])            # still a repeat within the window

    def test_failover_coalesced(self):
        self.L.publish([{"kind": "exit_failover", "group": "main", "from_tier": "t1", "to_tier": "t2", "moved": 2,
                         "back": False}], {}, {"main": "M"}, {"exits": {}}, self.at(0))
        self.L.publish([{"kind": "exit_failover", "group": "main", "from_tier": "t1", "to_tier": "t2", "moved": 3,
                         "back": False}], {}, {"main": "M"}, {"exits": {}}, self.at(0.5))
        self.assertEqual(self.kinds(), [])
        self.L.publish([], {}, {}, {"exits": {}}, self.at(1.1))
        self.assertEqual(self.kinds(), ["exit_failover"])
        self.assertEqual(self.L.events[0]["args"]["moved"], 5)

    def test_the_feed_keeps_500_events_of_7_days(self):
        """SE §3.6 (REV-s1-t4 З5): the feed of stage 1 — the last 500 events within 7 days (3.3.5: 50 of 24 h), by
        the behaviour, not by the constants."""
        for i in range(520):
            self.L._event("inbound_up", "gw_live.event.inbound_up", {"title": "x%d" % i}, self.at(i / 100.0))
        self.L._prune(self.wall[0])
        self.assertEqual(len(self.L.events), 500)
        self.assertEqual(self.L.events[0]["args"]["title"], "x20")              # the oldest went
        self.L.events = []
        old = self.at(0)
        self.L._event("inbound_up", "gw_live.event.inbound_up", {"title": "six days"}, old)
        self.L._event("inbound_up", "gw_live.event.inbound_up", {"title": "eight days"}, old - 2 * 86400)
        self.L._prune(old + 6 * 86400)                       # 6 days later: the first stays, the other is 8 days old
        self.assertEqual([e["args"]["title"] for e in self.L.events], ["six days"])
        self.L._prune(old + 7 * 86400 + 60)
        self.assertEqual(self.L.events, [])

    def test_node_online_first_and_the_open_outage_stays(self):
        self.L.publish([{"kind": "node_offline", "since": 10}], {}, {}, {"exits": {}}, self.at(0))
        for i in range(live.EVENTS_MAX + 5):
            self.L._event("inbound_up", "gw_live.event.inbound_up", {"title": "x"}, self.at(1 + i / 1000.0))
        self.L._prune(self.wall[0])
        self.assertEqual(len(self.L.events), live.EVENTS_MAX)
        self.assertEqual(self.L.events[0]["kind"], "node_offline")             # the outage going on is not pushed out
        self.L.publish([{"kind": "exit_dead", "exit": "e-1"}, {"kind": "node_online", "since": 99, "until": 500,
                                                              "minutes": 1}], {"e-1": "X"}, {}, {"exits": {}},
                       self.at(10))
        kinds = self.kinds()
        self.assertLess(kinds.index("node_online"), kinds.index("exit_dead"))
        on = [e for e in self.L.events if e["kind"] == "node_online"][0]
        self.assertEqual(on["args"]["since"], 10)                 # from the first node_offline (З2)


class NoExitTest(S1World):
    def test_no_exit_of_the_last_step(self):
        dec = {"groups": {"main": {"steps": {"d-1": "last_block", "d-2": "exit"}, "last_since": 77}}}
        self.assertEqual(live.no_exit(dec), {"d-1": {"step": "last_block", "group": "main", "since": 77}})


class XrayOrderTest(S1World):
    def test_the_number_of_the_render_and_the_fingerprint(self):
        """§2.5, §2.7: the first tick of the process renders and compares with the disk; later — only when the
        fingerprint differs from xray.decision or the counter xray-seq is ahead (``xray.render_wanted``); the number is
        taken under the state lock (``xray.next_seq``) and goes with the render."""
        calls, wanted = [], [False]
        seqs = iter(range(7, 100))

        def xa(st, sec, runner, pool, **kw):
            calls.append(kw)
            return {}
        with mock.patch.object(xray, "apply", xa), \
                mock.patch.object(xray, "render_wanted", lambda fp: wanted[0], create=True), \
                mock.patch.object(xray, "next_seq", lambda: next(seqs), create=True):
            self.round()
            self.assertEqual([c["seq"] for c in calls], [7])         # the start: one render always
            self.assertTrue(calls[0]["fp"])
            self.round()
            self.assertEqual(len(calls), 1)                          # nothing changed: no render
            wanted[0] = True                                         # ops._apply drew another decision / a seq lost
            self.round()
            self.assertEqual([c["seq"] for c in calls], [7, 8])

    def test_the_fingerprint_follows_the_targets_of_xray_users(self):
        st = state.load()
        pool = exits.pool(self.w.runner)
        d1 = exits.decision(st, pool_exits=pool, alive={self.vid, self.aid})
        self.assertEqual(collect.fingerprint(st, d1, pool), collect.fingerprint(st, d1, pool))
        d2 = dict(d1, quota_off=[self.vid])
        self.assertNotEqual(collect.fingerprint(st, d1, pool), collect.fingerprint(st, d2, pool))


class CountryRevTest(S1World):
    def test_the_first_country_does_not_change_rev(self):
        """Проверка 16: the first country seen and ``notified`` are out of the fingerprint — ``rev`` stays."""
        st = state.load()
        st["gateway"]["exit_policy"] = P.default_doc("compat")
        state.save(st)
        st = state.load()
        rev.note(st)
        state.save(st)
        st = state.load()
        before = rev.rev_of(st)
        wrote, later = collect._requests(st, [{"kind": "country_first", "exit": self.aid, "loc": "NL", "request": True},
                                              {"kind": "country_notified", "exit": self.aid, "loc": "DE",
                                               "request": True},
                                              {"kind": "geo_pass", "exit": self.aid, "request": True}])
        self.assertTrue(wrote)
        self.assertEqual(later, [("geo_pass", self.aid, 0)])
        self.assertEqual(model.exit_pref(st, self.aid)["country"]["expected"], "NL")
        self.assertEqual(rev.note(st), before)


class GeoKeptTest(S1World):
    def test_the_geo_pass_keeps_the_country_of_the_node(self):
        """The coordinator 19.10: the collector keeps ``direct_loc`` of the geo pass in ``gateway.hostmon.geo``
        (the check of the server, the price and the repeat read it); out of the fingerprint — ``rev`` stays."""
        self.round()
        st = state.load()
        before = rev.rev_of(st)
        self.assertNotIn("geo", st["gateway"].get("hostmon") or {})
        with mock.patch.object(collect, "_results", return_value=({}, [], {"direct_loc": "RU", "locs": {}})):
            self.round()
        st = state.load()
        g = st["gateway"]["hostmon"]["geo"]
        self.assertEqual((g["country"], g["ru"], g["src"]), ("RU", True, "exits_geo"))
        self.assertEqual(rev.rev_of(st), before)
        with mock.patch.object(collect, "_results", return_value=({}, [], {"direct_loc": None, "locs": {}})):
            self.round()
        self.assertEqual(state.load()["gateway"]["hostmon"]["geo"]["country"], "RU")   # no answer: kept as it is


class MinuteTest(S1World):
    def test_metrics_and_the_clock(self):
        """§3.5, §3.6: each change of a state — «transition» in metrics.db.events before the antispam; m1 once a minute;
        traffic.json only with the clock synchronised (T3, C2)."""
        from kaskad.gw import metrics
        self.write("/sys/class/net/kawg2/statistics/rx_bytes", "1000\n")
        self.write("/sys/class/net/kawg2/statistics/tx_bytes", "2000\n")
        with mock.patch.object(collect, "_clock_synced", lambda runner: False):
            for _ in range(3):
                self.round()
            self.w.leg(2, probe=False)
            for _ in range(8):
                self.round()
        self.assertFalse(os.path.exists(paths.traffic_json()))
        rows = metrics.transitions(self.aid, now=self.now)
        self.assertTrue(rows["items"], rows)
        self.assertEqual(rows["items"][0]["to"], "off")
        with mock.patch.object(collect, "_clock_synced", lambda runner: True):
            self.round(61)
        self.assertTrue(os.path.exists(paths.traffic_json()))

    def test_the_clock_not_synchronised_is_said(self):
        """REV-s1-t4 З3: the clock not synchronised — a line in the journal (once a day, not every minute), after an
        hour a problem of the gateway when an exit has a quota; synchronised — a line, no problem."""
        self.write("/sys/class/net/kawg2/statistics/rx_bytes", "1000\n")
        self.write("/sys/class/net/kawg2/statistics/tx_bytes", "2000\n")
        st = state.load()
        st["gateway"].setdefault("exits", {})[self.aid] = {"quota": {"period": "month", "limit_gb": 10,
                                                                     "reset_day": 1, "warn_pct": 80, "action": "off"}}
        state.save(st)
        with mock.patch.object(collect, "_clock_synced", lambda runner: False):
            for _ in range(5):
                self.round(61)
            self.assertEqual(self.log().count("часы сервера не синхронизированы"), 1)
            doc = json.loads(self.read("/run/kaskad/live.json"))
            self.assertEqual([i for i in doc["gateway"]["issues"] if i["kind"] == "gw_clock"], [])    # < 1 h
            for _ in range(60):
                self.round(61)
            doc = json.loads(self.read("/run/kaskad/live.json"))
            got = [i["detail"] for i in doc["gateway"]["issues"] if i["kind"] == "gw_clock"]
            self.assertEqual(got, ["Часы сервера не синхронизированы: квоты пока не считаются. Включите службу "
                                   "времени: timedatectl set-ntp true (chrony или systemd-timesyncd)"])   # B4 З2
            self.assertEqual(self.log().count("часы сервера не синхронизированы"), 1)
            st = state.load()                                      # no quota: nothing to say on the panel
            st["gateway"]["exits"][self.aid].pop("quota")
            state.save(st)
            self.round(61)
            doc = json.loads(self.read("/run/kaskad/live.json"))
            self.assertEqual([i for i in doc["gateway"]["issues"] if i["kind"] == "gw_clock"], [])
        with mock.patch.object(collect, "_clock_synced", lambda runner: True):
            self.round(61)
        self.assertIn("часы сервера синхронизированы — трафик выходов и квоты снова считаются", self.log())
        self.assertIsNone(collect.MEM.unsync_since)

    def test_a_quota_push_writes_traffic_json_at_once(self):
        """§5.6, T3 (REV-s1-t4 З5): the 80 % / 100 % marks of a quota go to traffic.json AT ONCE (``force``), though a
        plain write was a moment ago — a crash before the next write of the minute must not push them again."""
        from kaskad import tz
        from kaskad.gw import pins as PN
        q = {"period": "month", "limit_gb": 1, "reset_day": 1, "warn_pct": 80, "action": "off"}
        doc = collect._traffic_doc()
        PN.traffic_add(doc, self.aid, 0, 0, self.now, q, tz.zone())
        PN.traffic_add(doc, self.aid, 850 * 10 ** 6, 0, self.now + 60, q, tz.zone())
        collect._save_traffic(force=True)                    # written just now: a plain write waits SLOW_S
        self.assertEqual(PN.traffic_read()["exits"][self.aid].get("warned") or {}, {})
        got = collect._quota_obs(state.load(), self.aid, {"quota": q}, self.now + 60)
        self.assertEqual(got["warned"], [])                   # due now: pushed this round, not «told before»
        warned = PN.traffic_read()["exits"][self.aid]["warned"]
        self.assertEqual(list(warned.values()), [[80]])
        got = collect._quota_obs(state.load(), self.aid, {"quota": q}, self.now + 70)
        self.assertEqual(got["warned"], [80])                 # told once

    def test_an_exit_off_by_its_quota_is_in_quota_off(self):
        """§2.7, §5.6, О8 (REV-s1-t4 З5): an exit over its quota with «remove» — ``decision["quota_off"]`` of the tick
        and of a reader (the contract of T5: xray does not draw it); below the limit — not there."""
        from kaskad import tz
        from kaskad.gw import pins as PN
        q = {"period": "month", "limit_gb": 1, "reset_day": 1, "warn_pct": 80, "action": "remove"}
        st = state.load()
        st["gateway"].setdefault("exits", {})[self.aid] = {"quota": q}
        state.save(st)
        for _ in range(3):
            self.round()
        self.assertEqual(collect.MEM.decision["quota_off"], [])
        doc = collect._traffic_doc()
        PN.traffic_add(doc, self.aid, 0, 0, self.now, q, tz.zone())
        PN.traffic_add(doc, self.aid, 2 * 10 ** 9, 0, self.now + 1, q, tz.zone())
        collect._save_traffic(force=True)
        for _ in range(2):
            self.round()
        self.assertEqual(collect.MEM.decision["quota_off"], [self.aid])
        self.assertEqual(exits.decision(state.load())["quota_off"], [self.aid])
        self.assertEqual(xray.quota_off(collect.MEM.decision), {self.aid})

    def test_the_window_of_health_survives_a_restart(self):
        """§3.2: the window of «упал» lives in /run — a restarted collector goes on with it (as 3.3.5)."""
        for _ in range(3):
            self.round()
        self.w.leg(2, probe=False)
        self.round()
        tries = list(collect.MEM.health[self.aid]["tries"])
        self.assertTrue(tries)
        collect.MEM.reset()
        self.round(0)
        self.assertGreaterEqual(len(collect.MEM.health[self.aid]["tries"]), len(tries))


class HooksTest(S1World):
    def test_down_takes_the_address_out_of_ruonly_too(self):
        st = state.load()
        seen = []
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            if argv[:1] == ["nft"] and input:
                seen.append(input)
            return real(argv, input=input, **kw)
        with mock.patch.object(self.w.runner, "run", run):
            net.del_element(self.w.runner, st, "10.77.1.10")
            net.set_element(self.w.runner, st, "10.77.1.10", exits.MARK_BLOCK, ruonly=True)
        text = "".join(seen)
        self.assertIn("delete element ip kaskad gw_ruonly { 10.77.1.10 }", text)
        self.assertIn("add element ip kaskad gw_ruonly { 10.77.1.10 }", text)
        self.assertIn("add element ip kaskad gw_live { 10.77.1.10 }", text)
        seen[:] = []                                            # not the fallback block: never into gw_ruonly
        with mock.patch.object(self.w.runner, "run", run):
            net.set_element(self.w.runner, st, "10.77.1.10", 0x102, ruonly=False)
        self.assertNotIn("add element ip kaskad gw_ruonly", "".join(seen))

    def test_moved_off_a_removed_exit_forgets_its_connections(self):
        st = state.load()
        pool = exits.pool(self.w.runner)
        d = {"moves": [{"device": self.dev()["id"], "group": "main", "from": self.aid, "to": self.vid, "why": "evict"}],
             "states": {self.aid: "off", self.vid: "ok"}, "policy": P.load(st, pool)}
        got = []
        with mock.patch.object(net, "flush_devices", lambda runner, ips, mark: got.append((sorted(ips), mark)) or 1):
            collect._flush_moved(self.w.runner, st, d, pool)
            d["states"][self.aid] = "degraded"                       # without drop_on_degrade: kept
            collect._flush_moved(self.w.runner, st, d, pool)
        self.assertEqual(got, [(["10.77.1.10"], 0x102)])
