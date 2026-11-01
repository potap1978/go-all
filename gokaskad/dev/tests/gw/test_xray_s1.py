"""T5 — the xray path of stage 1 (SPEC-stage1-exits §2.2–2.7, §5.2, §5.6, §11.1 «xray.py»): the config by the decision
(users pinned to exits, fail-block, API and stats always, probe-<id>, pool-socks), live changes instead of restarts
(pins, deaths, the pool, the quota), restarts only on purpose (a block by choice, a leaving user), the xray lock, the
number of the render, the deferred restart, the real ``xray -test`` of the new configs."""

import copy
import json
import os
import time
import unittest
from unittest import mock

from kaskad import paths, util
from kaskad.gw import exits, xray
from kaskad.gw.inbounds.base import DummyInbound
from kaskad.gw import registry
from tests import realbin
from tests.gw.s1_xray import S1Case, chain_of, rules_of


class RenderTest(S1Case):
    def test_user_rule_goes_to_the_pinned_exit(self):
        self.node()
        cfg = self.render()
        tel = chain_of(cfg, "telefon-0001")
        self.assertEqual(len(tel), 1)                                  # the chain: the route rule alone (lists — ls-)
        self.assertEqual(tel[0]["outboundTag"], exits.tag(self.vid))
        self.assertTrue(tel[0]["ruleTag"].startswith("sk-telefon-0001-"))
        moved = self.render(self.dec(pins={"d-00000001": self.bid, "d-00000003": self.vid}))
        self.assertEqual(chain_of(moved, "telefon-0001")[0]["outboundTag"], exits.tag(self.bid))
        self.assertNotIn("gw-all-direct", json.dumps(cfg))           # no balancer per fail policy any more
        self.assertNotIn("-direct\"", json.dumps([b["tag"] for b in cfg["routing"]["balancers"]]))

    def test_fail_block_before_block_and_block_first_for_hot_inbounds(self):
        self.node()
        cfg = self.render()
        tags = [o["tag"] for o in cfg["outbounds"]]
        self.assertEqual(tags[0], "block")                             # the first outbound of the hot inbounds
        self.assertLess(tags.index(xray.FAIL_BLOCK), len(tags))
        self.assertEqual(tags[-2:], ["direct", xray.FAIL_BLOCK])
        self.node(socks=False)
        tags = [o["tag"] for o in self.render()["outbounds"]]
        self.assertEqual(tags[-3:], ["direct", xray.FAIL_BLOCK, "block"])  # no hot inbound: block stays the last

    def test_api_stats_probes_pool_socks_without_vless_inbound(self):
        self.node(socks=False)                                         # only the pool: no VLESS, no SOCKS5
        cfg = self.render()
        self.assertEqual(cfg["api"]["services"], ["StatsService", "HandlerService", "RoutingService"])
        self.assertEqual(cfg["stats"], {})
        self.assertEqual(cfg["policy"]["system"], {"statsOutboundUplink": True, "statsOutboundDownlink": True})
        self.assertNotIn("statsInboundUplink", json.dumps(cfg))       # T0 С0.13: they kill the counters of users
        ins = dict((i["tag"], i) for i in cfg["inbounds"])
        self.assertEqual((ins["pool-socks"]["listen"], ins["pool-socks"]["port"]), ("127.0.0.1", 12699))
        for eid, n in ((self.vid, 1), (self.bid, 3)):
            ib = ins["probe-" + eid]
            self.assertEqual((ib["listen"], ib["port"], ib["protocol"], ib["settings"]["udp"]),
                             ("127.0.0.1", 12700 + n, "socks", False))
            route = rules_of(cfg, inboundTag=["tproxy-" + eid, "probe-" + eid], outboundTag=exits.tag(eid))
            self.assertEqual(len(route), 1)
            self.assertTrue(rules_of(cfg, inboundTag=["tproxy-" + eid, "probe-" + eid], outboundTag="block"))
        self.assertNotIn("probe-" + self.aid, ins)                    # AmneziaWG: SO_MARK, no SOCKS inbound
        self.assertEqual(rules_of(cfg, inboundTag=["pool-socks"])[0]["balancerTag"], "gw-all")
        blocked = rules_of(cfg, outboundTag="block", inboundTag=["tproxy-in", "pool-socks"])
        self.assertTrue(blocked and "127.0.0.0/8" in blocked[0]["ip"])

    def test_gw_vless_main_only_and_per_connection(self):
        self.node(socks=False)
        st = self.st
        st["gateway"]["exit_policy"] = {"version": 1, "groups": [
            {"id": "main", "role": "abroad", "preset": "compat",
             "tiers": [{"id": "t1", "exits": [self.vid, self.aid]}]},
            {"id": "g-00aa11", "role": "abroad", "preset": "compat", "tiers": [{"id": "t1", "exits": [self.bid]}]}]}
        bal = dict((b["tag"], b) for b in self.render()["routing"]["balancers"])
        self.assertEqual(bal["gw-vless"]["selector"], ["gx-"])          # nobody goes there: stable under the pool
        self.assertEqual(bal["gw-all"]["selector"], ["gx-"])
        st["gateway"]["exit_policy"]["groups"][0]["per_connection"] = True
        st["gateway"]["exit_policy"]["groups"][0]["tiers"][0]["exits"].append(self.bid)
        st["gateway"]["exit_policy"]["groups"].pop()
        dec = self.dec(states={self.vid: "standby", self.bid: "ok", self.aid: "ok"})
        bal = dict((b["tag"], b) for b in self.render(dec)["routing"]["balancers"])
        self.assertEqual(bal["gw-vless"]["selector"], [exits.tag(self.bid)])     # the good ones of the active tier

    def test_quota_off_exit_is_not_drawn(self):
        self.node()
        cfg = self.render(self.dec(quota_off=[self.bid]))
        text = json.dumps(cfg)
        for t in (exits.tag(self.bid), "tproxy-" + self.bid, "probe-" + self.bid, "px-" + self.bid):
            self.assertNotIn(t, text)
        self.assertIn(exits.tag(self.vid), text)
        dev = self.render(self.dec(quota_off=[self.vid]))              # a pin to it (T4 would not): closed, no leak
        self.assertEqual(chain_of(dev, "telefon-0001")[0]["outboundTag"], xray.FAIL_BLOCK)

    def test_ls_rules_shared_and_never_empty(self):
        self.node()
        cfg = self.render()
        ls = rules_of(cfg, prefix="ls-")
        self.assertTrue(ls and all(r["user"] for r in ls))
        first_chain = min(i for i, r in enumerate(cfg["routing"]["rules"]) if str(r.get("ruleTag", ""))[:3] == "sk-")
        self.assertTrue(all(cfg["routing"]["rules"].index(r) < first_chain for r in ls))       # above the chains
        for d in self.st["gateway"]["devices"]:
            d["rules"] = "all_exit"                                   # nobody «Россия напрямую»
        cfg = self.render()
        self.assertEqual(rules_of(cfg, prefix="ls-"), [])
        self.assertFalse([r for r in cfg["routing"]["rules"] if r.get("outboundTag") == "direct" and "user" not in r
                          and r.get("inboundTag") != ["gw-dns-int"]])
        with mock.patch.object(xray.rules_apply, "xray_rules",
                               lambda *a, **kw: [{"type": "field", "inboundTag": ["gw-socks5"], "user": [],
                                                  "domain": ["ext:x.dat:y"], "outboundTag": "direct",
                                                  "ruleTag": "ls-0"}]):
            with self.assertRaises(AssertionError):                    # З9: a rule of the lists for EVERY user
                self.render()

    def test_ls_count_does_not_grow_with_users(self):
        self.node()
        n1 = len(rules_of(self.render(), prefix="ls-"))
        for i in range(4, 40):
            d = copy.deepcopy(self.dev("d-00000001"))
            d.update(id="d-%08d" % i, login="extra-%04d" % i, ips={"wg": "10.77.1.%d" % (20 + i)})
            self.st["gateway"]["devices"].append(d)
            self.sec["dev.%s.socks5.password" % d["id"]] = "pw-%d" % i
        cfg = self.render(self.dec(pins=dict([("d-00000001", self.vid), ("d-00000003", self.vid)] +
                                             [("d-%08d" % i, self.vid) for i in range(4, 40)])))
        self.assertEqual(len(rules_of(cfg, prefix="ls-")), n1)
        self.assertEqual(len(rules_of(cfg, prefix="sk-")), 39)

    def test_hot_prefixes_from_the_adapters(self):
        class Hy(DummyInbound):
            xray_hot = True
            xray_hot_prefix = "h2-"
        registry.register(Hy(kind="hy2"))
        self.assertEqual(xray.hot_prefixes(), ("h2-", "sk-", "vl-"))

    def test_internal_ports_of_stage_1(self):
        self.node(socks=False)
        ports = set(p for pr, p, _ in xray.internal_ports(self.st, self.pool, self.w.runner, self.dec()) if pr == "tcp")
        self.assertTrue({12699, 12701, 12703, 10854, 10852} <= ports)  # API without a VLESS inbound
        self.assertNotIn(12702, ports)                                 # AmneziaWG: no probe-<id>
        # without a decision — exits.decision(st) (§2.4, REV-s1-t5 С1, З2): the same ports of stage 1, never 3.3.5
        default = set(p for pr, p, _ in xray.internal_ports(self.st, self.pool, self.w.runner) if pr == "tcp")
        self.assertTrue({12699, 12701, 12703, 10854} <= default)
        self.assertNotIn(12702, default)
        self.assertEqual(xray.guarded_ports(self.render()), [10852, 10854, 12699, 12701, 12703])

    def test_internal_ports_api_of_a_socks5_only_node(self):
        """REV-s1-t5 З2: a node with SOCKS5 and no exits draws the API 10854 (``api = bool(hot)``) — reserved too."""
        self.node()
        self.sec.dirty = True                                          # internal_ports reads the stored secrets
        self.sec.save()
        cfg = xray.render(self.st, self.sec, [], (), self.dec(states={}, pins={}, gw_all=None))
        self.assertIn(10854, xray.guarded_ports(cfg))
        ports = set(p for pr, p, _ in xray.internal_ports(self.st, [], self.w.runner) if pr == "tcp")
        self.assertIn(10854, ports)
        self.assertFalse({12699, 12701, 12703} & ports)                # no exit: no pool inbound, no probes


@unittest.skipUnless(realbin.xray_path(), "SKIP нет xray: bash tools/fetch_test_bins.sh")
class RealXrayTest(S1Case):
    """``xray run -test`` of the real 26.3.27 on the configs of stage 1 (§11.1)."""

    def check(self, cfg):
        ok, text = realbin.xray_check(cfg)
        self.assertTrue(ok, text[-800:])

    def test_pool_only_api_probes(self):
        self.node(socks=False)
        self.check(self.render())

    def test_socks_lists_fail_block_and_quota(self):
        self.node()
        self.check(self.render())
        self.check(self.render(self.dec(steps={"d-00000001": "last_block", "d-00000003": "last_direct"},
                                        quota_off=[self.bid])))


class HotPlanTest(S1Case):
    def hot(self, old, new, **kw):
        return xray.hot_plan(old, new, ["gw-socks5"], modes={"gw-socks5": None}, **kw)

    def test_pin_moves_live(self):
        self.node()
        old = self.render()
        new = self.render(self.dec(pins={"d-00000001": self.bid, "d-00000003": self.vid}))
        plan = self.hot(old, new)
        self.assertIsNotNone(plan)
        # REV-s1-t5 З7: one outcome per scenario. «Россия напрямую» (an updated node): the user is in the shared lists
        # ``ls-`` of its target — its move changes their lists of users: all the rules anew, live (§2.4)
        self.assertEqual(plan["routing"], new["routing"])
        for d in self.st["gateway"]["devices"]:                       # no lists (all through the exit, no ads block)
            d["rules"] = "all_exit"
        self.st["gateway"]["rules"] = {"default": "all_exit", "block_ads": False, "block_torrent": False}
        old = self.render()
        new = self.render(self.dec(pins={"d-00000001": self.bid, "d-00000003": self.vid}))
        self.assertFalse(rules_of(new, xray.LS_PREFIX))
        plan = self.hot(old, new)
        self.assertIsNone(plan["routing"])                            # the chain whole: rm the old, append the new
        self.assertEqual([r["user"] for r in plan["add_rules"]], [["telefon-0001"]])
        self.assertEqual(len(plan["rm_rules"]), 1)
        self.assertTrue(plan["rm_rules"][0].startswith("sk-telefon-0001-"))

    def test_death_and_no_good_exit_are_live(self):
        self.node()
        old = self.render()
        dead = self.render(self.dec(steps={"d-00000001": "last_block", "d-00000003": "last_block"}))
        self.assertEqual(chain_of(dead, "telefon-0001")[0]["outboundTag"], xray.FAIL_BLOCK)
        self.assertIsNotNone(self.hot(old, dead))                     # gx-E -> fail-block: no restart
        self.assertIsNotNone(self.hot(dead, old))

    def test_block_by_choice_pause_revoke_restart(self):
        self.node()
        old = self.render()
        self.dev("d-00000001")["route"] = {"via": "block", "fail": "block"}
        self.assertIsNone(self.hot(old, self.render()))               # its connections must go: a restart
        self.assertTrue(xray.leaves(old, self.render(), ["gw-socks5"]))
        self.dev("d-00000001")["route"] = None
        self.dev("d-00000001")["enabled"] = False                     # a pause: the account leaves
        self.assertIsNone(self.hot(old, self.render()))
        self.dev("d-00000001")["enabled"] = True
        self.sec["dev.d-00000001.socks5.password"] = "another"         # a new password: a restart (rmu is never called)
        self.assertIsNone(self.hot(old, self.render()))

    def test_new_socks_account_restarts_vless_user_goes_live(self):
        self.node()
        old = self.render()
        d = copy.deepcopy(self.dev("d-00000001"))
        d.update(id="d-00000009", login="novyi-0009", ips={"wg": "10.77.1.40"})
        self.st["gateway"]["devices"].append(d)
        self.sec["dev.d-00000009.socks5.password"] = "pw-9"
        new = self.render(self.dec(pins={"d-00000001": self.vid, "d-00000003": self.vid, "d-00000009": self.vid}))
        self.assertIsNone(self.hot(old, new))                         # T0 С0.7: adu on socks — not a UserManager
        vl_old, vl_new = copy.deepcopy(old), copy.deepcopy(new)
        for c in (vl_old, vl_new):                                    # the same with a VLESS inbound: adu, live
            for ib in c["inbounds"]:
                if ib["tag"] == "gw-socks5":
                    ib["protocol"] = "vless"
                    ib["settings"] = {"clients": [{"email": a["user"], "id": a["pass"]} for a in ib["settings"]["accounts"]],
                                      "decryption": "none"}
        plan = xray.hot_plan(vl_old, vl_new, ["gw-socks5"])
        self.assertEqual([c["email"] for _, c in plan["add_users"]], ["novyi-0009"])
        self.assertEqual(plan["modes"], {"gw-socks5": "cli"})

    def test_pool_change_and_quota_are_live(self):
        self.node()
        old = self.render()
        off = self.render(self.dec(quota_off=[self.bid]))
        plan = self.hot(old, off)
        self.assertEqual((plan["rm_outbounds"], plan["rm_inbounds"]),
                         ([exits.tag(self.bid)], sorted(["probe-" + self.bid, "tproxy-" + self.bid])))
        self.assertEqual(len(plan["rm_rules"]), 3)
        self.assertTrue(all(t.startswith("px-" + self.bid) for t in plan["rm_rules"]))
        self.assertEqual((plan["add_outbounds"], plan["add_inbounds"], plan["routing"]), ([], [], None))
        back = self.hot(off, old)                                      # the day of the reset: back, live
        self.assertEqual([o["tag"] for o in back["add_outbounds"]], [exits.tag(self.bid)])
        self.assertEqual(sorted(i["tag"] for i in back["add_inbounds"]), sorted(["probe-" + self.bid,
                                                                                 "tproxy-" + self.bid]))
        self.assertEqual(len(back["add_rules"]), 3)

    def test_quota_of_an_exit_by_name_keeps_the_dns(self):
        from tests.fakes_gw import VLESS_A, VLESS_B, awg_line, write_pool
        self.node()
        named = VLESS_B.replace("198.51.100.21", "vl.example.com")
        write_pool([VLESS_A, awg_line(2, name="Amsterdam"), named])
        self.pool = exits.pool(self.w.runner)
        self.bid = exits.exit_id(named)
        old = self.render()
        off = self.render(self.dec(quota_off=[self.bid]))
        self.assertEqual(old["dns"], off["dns"])                     # the name stays resolved locally: no restart
        self.assertIn("full:vl.example.com", json.dumps(off["dns"]))
        self.assertIsNotNone(self.hot(old, off))

    def test_hysteria2_same_key_live_other_key_restart(self):
        self.node()
        old = self.render()
        hy = {"tag": exits.tag(self.bid), "protocol": "hysteria",
              "settings": {"address": "hy.example.com", "port": 8443, "version": 2}, "streamSettings": {}}

        def with_hy(cfg, auth):
            c = copy.deepcopy(cfg)
            c["outbounds"] = [dict(hy, settings=dict(hy["settings"], auth=auth)) if o["tag"] == hy["tag"] else o
                              for o in c["outbounds"]]
            return c
        a = with_hy(old, "k1")
        off = self.render(self.dec(quota_off=[self.bid]))
        self.assertIsNotNone(self.hot(off, a, seen={"hy.example.com:8443": xray._hy2_eps(a)["hy.example.com:8443"]}))
        self.assertIsNone(self.hot(off, with_hy(old, "k2"),
                                   seen={"hy.example.com:8443": xray._hy2_eps(a)["hy.example.com:8443"]}))
        self.assertIsNone(self.hot(a, with_hy(old, "k2")))             # its key changed in place

    def test_other_routing_changes_replace_all_rules(self):
        self.node()
        old = self.render()
        for d in self.st["gateway"]["devices"]:
            d["rules"] = "all_exit"                                   # the lists of everybody change
        new = self.render()
        plan = self.hot(old, new)
        self.assertEqual(plan["routing"], new["routing"])
        self.assertEqual((plan["add_rules"], plan["rm_rules"]), ([], []))
        many = copy.deepcopy(old)
        ext = [r for r in many["routing"]["rules"] if "ext:" in json.dumps(r)][0]
        for i in range(xray.HOT_MAX_EXT_RULES + 1):
            many["routing"]["rules"].insert(0, dict(ext, ruleTag="ls-x%d" % i))
        self.assertIsNone(self.hot(old, many))                        # Р28: more lists than one gRPC call

    def test_batches_and_the_limit_of_a_tick(self):
        self.node()
        old = self.render()
        new = copy.deepcopy(old)
        add = [{"type": "field", "inboundTag": ["gw-socks5"], "user": ["u%d" % i], "outboundTag": "direct",
                "ruleTag": "sk-u%d-x-0" % i} for i in range(450)]
        new["routing"]["rules"] += add
        plan = self.hot(old, new)
        self.assertEqual([len(b) for b in xray._batches(plan["add_rules"])], [200, 200, 50])
        new["routing"]["rules"] += [dict(r, ruleTag=r["ruleTag"] + "y") for r in add + add]
        self.assertIsNone(self.hot(old, new))                         # 1350 > HOT_MAX_TICK: a restart
        self.assertIsNone(self.hot(old, old))                         # nothing to do
        self.assertIsNone(xray.hot_plan(None, old, ["gw-socks5"]))
        no_api = copy.deepcopy(old)
        no_api["api"]["services"] = ["StatsService"]
        self.assertIsNone(self.hot(no_api, old))
        other = copy.deepcopy(old)
        other["outbounds"].append({"tag": "x", "protocol": "freedom"})
        self.assertIsNone(self.hot(old, other))                       # the base: a restart


class ApplyTest(S1Case):
    def apply(self, dec=None, **kw):
        return xray.apply(self.st, self.sec, self.w.runner, self.pool, self.dec() if dec is None else dec, **kw)

    def calls(self):
        return [v for v, _, _ in self.w.xray.api]

    def test_live_change_without_test_and_restart(self):
        self.node()
        for d in self.st["gateway"]["devices"]:
            d["rules"] = "all_exit"                                   # no lists: the chains alone change
        res = self.apply(seq=1, fp="a")
        self.assertEqual((res["started"], res.get("tested")), (True, True))
        tested = len(self.w.xray.tested)
        res = self.apply(self.dec(steps={"d-00000001": "last_block"}), seq=2, fp="b")
        self.assertEqual((res["changed"], res["restarted"], res.get("tested")), (True, False, None))
        self.assertEqual(len(self.w.xray.tested), tested)              # a pure live change: no second xray
        self.assertEqual(self.calls(), ["adrules", "rmrules"])
        self.assertEqual(xray.applied(), {"fp": "b", "seq": 2})

    def test_pool_change_live_and_bo_after_all_rules(self):
        self.node()
        self.apply(seq=1, fp="a")
        res = self.apply(self.dec(quota_off=[self.bid]), seq=2, fp="b")
        self.assertEqual((res["restarted"], res.get("tested")), (False, True))   # a new base part: tested, live
        self.assertEqual(self.calls(), ["rmrules", "rmi", "rmo"])
        del self.w.xray.api[:]
        for d in self.st["gateway"]["devices"]:
            d["rules"] = "all_exit"
        res = self.apply(self.dec(quota_off=[self.bid], gw_all="gx-" + self.vid), seq=3, fp="c")
        self.assertEqual(self.calls(), ["adrules", "bo"])
        self.assertNotIn("-append", self.w.xray.api[0][1])
        self.assertEqual(self.w.xray.override, {"gw-all": "gx-" + self.vid})

    def test_block_by_choice_restarts(self):
        self.node()
        self.apply(seq=1)
        self.dev("d-00000001")["route"] = {"via": "block", "fail": "block"}
        res = self.apply(seq=2)
        self.assertEqual(res["restarted"], True)
        self.assertEqual(res["notes"], [xray.CUT_NOTE])

    def test_deferred_restart_keeps_the_file(self):
        self.node()
        # the device is not on «Россия напрямую»: a change that takes direct rules away is never deferred (B6,
        # REV-s1-b5 П4 — tests/gw/test_s1_b6.py)
        self.dev("d-00000001")["rules"] = "all_exit"
        self.apply(seq=1, fp="a")
        before = util.read_text(xray.config_path())
        self.dev("d-00000001")["route"] = {"via": "block", "fail": "block"}
        res = self.apply(seq=2, fp="b", defer_restart=True)
        self.assertEqual((res.get("deferred"), res["restarted"], res["changed"]), (True, False, False))
        self.assertEqual(util.read_text(xray.config_path()), before)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(xray.config_path()), "xray.new.json")))
        self.assertEqual(xray.applied(), {"fp": "a", "seq": 1})
        res = self.apply(seq=3, fp="b")                               # the gap passed: now
        self.assertTrue(res["restarted"])
        self.assertEqual(xray.applied(), {"fp": "b", "seq": 3})

    def test_stale_render_is_dropped(self):
        self.node()
        self.apply(seq=5, fp="manual")
        self.dev("d-00000001")["enabled"] = False                     # the tick drew it by an older state
        res = self.apply(seq=4, fp="tick")
        self.assertEqual(res.get("stale"), True)
        self.assertIn("telefon-0001", util.read_text(xray.config_path()))      # the fresher render stays in xray
        self.assertEqual(xray.applied(), {"fp": "manual", "seq": 5})

    def test_seq_counter_and_render_wanted(self):
        self.node()
        self.assertEqual((xray.next_seq(), xray.next_seq()), (1, 2))
        self.assertTrue(xray.render_wanted("x"))
        xray.note_applied("x", 2)
        self.assertFalse(xray.render_wanted("x"))
        xray.next_seq()                                                # taken, never applied (a lock timeout)
        self.assertTrue(xray.render_wanted("x"))
        self.assertEqual(os.stat(paths.xray_seq()).st_mode & 0o777, 0o600)

    def test_lock_timeout_keeps_state_and_warns(self):
        self.node()
        held = xray._lock(1)
        try:
            t = time.monotonic()
            res = self.apply(seq=1, lock_wait=0.3)
            self.assertLess(time.monotonic() - t, 2)
        finally:
            xray._unlock(held)
        self.assertTrue(res["lock_timeout"])
        self.assertIn("xray применит их в течение 20 с", res["warnings"][0])
        self.assertFalse(os.path.exists(xray.config_path()))
        res = self.apply(seq=1)                                       # the next call applies it
        self.assertTrue(res["started"])

    def test_two_processes_do_not_mix(self):
        """The second apply waits for the lock of the first (``fcntl.flock`` — another open file, as another process)."""
        self.node()
        order = []
        real = xray._apply_locked

        def slow(*a, **kw):
            order.append("in")
            time.sleep(0.2)
            r = real(*a, **kw)
            order.append("out")
            return r
        import threading
        with mock.patch.object(xray, "_apply_locked", slow):
            t = threading.Thread(target=lambda: self.apply(seq=1))
            t.start()
            time.sleep(0.05)
            self.apply(seq=2)
            t.join()
        self.assertEqual(order, ["in", "out", "in", "out"])

    def test_api_time_limit_means_restart(self):
        self.node()
        self.apply(seq=1)
        clock = [0.0]

        def mono():
            clock[0] += xray.HOT_LOCK_S / 2 + 1                       # every look at the clock: half the limit
            return clock[0]
        with mock.patch.object(xray, "_mono", mono):
            res = self.apply(self.dec(quota_off=[self.bid]), seq=2)
        self.assertTrue(res["restarted"])                             # the 2nd call is over HOT_LOCK_S: a restart
        self.assertEqual(self.calls(), ["rmrules"])

    def test_rejected_config_is_not_tested_again_soon(self):
        self.node()
        self.apply(seq=1)
        self.w.xray.fail = "Failed to start: bad\n"
        dec = self.dec(quota_off=[self.bid])
        from kaskad.errors import KaskadError
        n0 = len(self.w.xray.tested)
        with self.assertRaises(KaskadError):
            self.apply(dec, seq=2, skip_rejected=True)
        n = len(self.w.xray.tested)
        self.assertGreater(n, n0)                                      # tested (the fake counts a refusal: З1)
        with self.assertRaises(KaskadError):
            self.apply(dec, seq=3, skip_rejected=True)
        self.assertEqual(len(self.w.xray.tested), n)                   # the same text: no second xray -test
        self.assertEqual(xray.rejected_info()["error"], "Failed to start: bad")

    def test_restart_due_and_storm(self):
        self.node()
        self.assertTrue(xray.restart_due())
        self.apply(seq=1)                                             # started: the stamp
        self.assertFalse(xray.restart_due())
        self.assertTrue(xray.restart_due(time.time() + xray.RESTART_GAP_S + 1))

    def test_override_and_info(self):
        self.node()
        self.assertTrue(xray.balancer_override(self.w.runner, "gx-" + self.vid))
        self.assertEqual(xray.balancer_info(self.w.runner), "gx-" + self.vid)
        self.assertTrue(xray.balancer_override(self.w.runner, None))
        self.assertEqual(xray.balancer_info(self.w.runner), "")
        xray.balancer_override(self.w.runner, "out_2", balancer="bot-bal", port=xray.EGRESS_API_PORT)
        self.assertIn("--server=127.0.0.1:10851", self.w.xray.api[-1][1])
        self.w.xray.api_fail = "bi"
        self.assertIsNone(xray.balancer_info(self.w.runner))


if __name__ == "__main__":
    unittest.main()
