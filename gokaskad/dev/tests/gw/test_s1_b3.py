"""Stage 1, step B3 — the rest of the safety of the network after B1 (REV-s1-b1, SEC-s1-b1): xray of stage 1 never
replaces its config while the live table has no gw_xports — one place for every caller (REV З1 / SEC З-1: rules/ops
.apply_system after a failed nft.apply, the timer of the lists, ops._apply, the collector); a pause between two full
applies for gw_xports (П1); «xray не нужен» under the xray lock (П2 / SEC П-1, «было до правки»); the full text of
nft.apply keeps the live gw_xports (SEC П-2); the mutants that survived B1 (П4: M4, M10, M25, M28)."""

import copy
import os
import re
from unittest import mock

from kaskad import nft, secrets, state, util
from kaskad.errors import KaskadError
from kaskad.gw import collect, exits, net, ops as gw_ops, peek, xray
from kaskad.rules import ops as rules_ops
from kaskad.sysrun import Result
from tests.gw.test_collect_s1 import S1World

IP = "10.77.1.10"


def _table_335(case):
    """The table of a node updated from 3.3.5: no gw_xports, no rule of gw_out with it."""
    rules = case.w.nft.table()["chains"]["gw_out"]["rules"]
    for i in reversed(range(len(rules))):
        if "@gw_xports" in str(rules[i]["expr"]):
            case.w.nft.drop_rule("gw_out", i)
    case.w.nft.run_script("delete set ip kaskad gw_xports\n", True)
    assert "gw_xports" not in case.w.nft.table()["sets"]


def _no_config():
    try:
        os.unlink(xray.config_path())
    except OSError:
        pass


def _open_ports(path):
    cfg = xray._read_config(path)
    return xray.guarded_ports(cfg) if isinstance(cfg, dict) else []


class NoXportsKeepsTheFileTest(S1World):
    """REV-s1-b1 З1 / SEC-s1-b1 З-1: ``xports_add`` False (no gw_xports) — the config of stage 1 with pool-socks and
    probe-<id> is not written, nothing restarts, the number stays wanted; with the table in place it goes in."""

    def setUp(self):
        super().setUp()
        _table_335(self)
        _no_config()

    def test_xray_apply_waits_for_the_table(self):
        st, sec = state.load(), secrets.load()
        pool = exits.pool(self.w.runner)
        n = xray.next_seq()
        res = xray.apply(st, sec, self.w.runner, pool, seq=n, fp="b3")
        self.assertTrue(res.get("xports_missing"))
        self.assertFalse(res["changed"] or res["started"] or res["restarted"])
        self.assertFalse(os.path.exists(xray.config_path()))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(xray.config_path()), "xray.new.json")))
        self.assertTrue(res.get("warnings"))
        self.assertNotEqual(xray.applied().get("fp"), "b3")
        self.assertTrue(xray.render_wanted("b3"))                     # the collector draws it again
        nft.apply(st, self.w.runner)                                   # the table first …
        res = xray.apply(st, sec, self.w.runner, pool, seq=xray.next_seq(), fp="b3")
        self.assertTrue(res["changed"])                                # … then xray of stage 1
        ports = _open_ports(xray.config_path())
        self.assertIn(xray.POOL_SOCKS_PORT, ports)
        self.assertLessEqual(set(str(p) for p in ports), set(self.w.nft.elements("gw_xports")))

    def test_rules_apply_system_after_a_failed_table(self):
        """The timer of the lists (``rules update`` → ``apply_system``): nft.apply fails — a warning, and xray keeps the
        file it had (none here: no config with the open SOCKS appears)."""
        with mock.patch.object(nft, "apply", mock.Mock(side_effect=KaskadError("nft refused"))):
            res = rules_ops.apply_system(self.w.runner)
        self.assertTrue(any("nft refused" in w for w in res["warnings"]))
        self.assertTrue(res["xray"].get("xports_missing"))
        self.assertFalse(res["xray"]["changed"])
        self.assertEqual(_open_ports(xray.config_path()), [])
        self.assertNotIn("gw_xports", self.w.nft.table()["sets"])

    def test_ops_apply_stops_at_the_table(self):
        """``ops._apply`` (gw apply): nft.apply raises before xray — xray.apply is never reached."""
        calls = []
        with mock.patch.object(nft, "apply", mock.Mock(side_effect=KaskadError("nft refused"))), \
                mock.patch.object(xray, "apply", lambda *a, **kw: calls.append(1) or {}):
            with self.assertRaises(KaskadError):
                gw_ops.apply_all(self.w.runner)
        self.assertEqual(calls, [])

    def test_a_failed_add_on_a_stage_1_table_keeps_the_file_too(self):
        nft.apply(state.load(), self.w.runner)
        self.w.nft.run_script("flush set ip kaskad gw_xports\n", True)
        with mock.patch.object(net, "xports_add", lambda *a: False):
            res = xray.apply(state.load(), secrets.load(), self.w.runner, exits.pool(self.w.runner))
        self.assertTrue(res.get("xports_missing"))
        self.assertFalse(os.path.exists(xray.config_path()))


class FullApplyPauseTest(S1World):
    """REV-s1-b1 П1: no gw_xports and nft.apply failing every time — the whole table at most once per XPORTS_RETRY_S
    (the map goes on), then again; once it worked, xray of stage 1 follows."""

    def test_one_full_apply_per_pause(self):
        _table_335(self)
        collect.MEM.reset()
        calls, xr = [], []
        fail = mock.Mock(side_effect=lambda *a, **kw: calls.append(1) or (_ for _ in ()).throw(KaskadError("nft")))
        with mock.patch.object(nft, "apply", fail), \
                mock.patch.object(xray, "apply", lambda *a, **kw: xr.append(1) or {}):
            for _ in range(getattr(collect, "XPORTS_RETRY_S", 60) // 10):
                self.round()
            self.assertEqual(len(calls), 1)
            self.round(dt=10)                                          # the pause over: tried again
            self.assertEqual(len(calls), 2)
        self.assertEqual(xr, [])                                       # xray waited all the time
        with mock.patch.object(xray, "apply", lambda *a, **kw: xr.append(1) or {}):
            self.round(dt=getattr(collect, "XPORTS_RETRY_S", 60))
        self.assertIn("gw_xports", self.w.nft.table()["sets"])
        self.assertEqual(xr, [1])


class NotNeededUnderTheLockTest(S1World):
    """REV-s1-b1 П2 / SEC-s1-b1 П-1 and «было до правки»: «xray не нужен» — the stop and the shrink of gw_xports under
    the xray lock; a render older than the applied one never stops a fresher xray."""

    def setUp(self):
        super().setUp()
        st, sec = state.load(), secrets.load()
        xray.apply(st, sec, self.w.runner, exits.pool(self.w.runner), seq=xray.next_seq(), fp="up")
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        self.ports = sorted(self.w.nft.elements("gw_xports"))
        self.assertIn(str(xray.POOL_SOCKS_PORT), self.ports)

    def apply_not_needed(self, **kw):
        with mock.patch.object(xray, "needed", lambda *a: False):
            return xray.apply(state.load(), secrets.load(), self.w.runner, exits.pool(self.w.runner), **kw)

    def test_the_lock_held_nothing_stopped_nothing_shrunk(self):
        held = xray._lock(0)                                           # «ops._apply» of another process
        try:
            res = self.apply_not_needed(lock_wait=0)
        finally:
            xray._unlock(held)
        self.assertTrue(res.get("lock_timeout"))
        self.assertFalse(res["stopped"])
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        self.assertEqual(sorted(self.w.nft.elements("gw_xports")), self.ports)
        res = self.apply_not_needed(lock_wait=0)                       # free: stopped, settled
        self.assertTrue(res["stopped"])
        self.assertEqual(self.w.nft.elements("gw_xports"), [str(xray.EGRESS_API_PORT)])

    def test_an_older_render_does_not_stop_a_fresher_xray(self):
        done = xray.applied()["seq"]
        res = self.apply_not_needed(seq=done - 1, fp="old")
        self.assertTrue(res.get("stale"))
        self.assertFalse(res["stopped"])
        self.assertTrue(self.w.sysd.units[xray.UNIT]["active"])
        self.assertEqual(sorted(self.w.nft.elements("gw_xports")), self.ports)
        self.assertEqual(xray.applied()["fp"], "up")


class FullTextKeepsLiveXportsTest(S1World):
    """SEC-s1-b1 П-2: the full text nft.apply falls back to recreates gw_xports as the plan ∪ the live set — the port of
    an exit just removed stays closed while the old xray (refused or deferred config) still opens it."""

    def test_the_stale_port_stays_through_a_recreate(self):
        st = state.load()
        nft.apply(st, self.w.runner)
        stale = str(xray.PROBE_PORT_BASE + 9)
        self.w.nft.run_script("add element ip kaskad gw_xports { %s }\n" % stale, True)
        real, seen = nft.check, []

        def check(script, runner):
            seen.append(script)
            if len(seen) == 1:
                return False, "the script refused"                   # the update script fails: the full text
            return real(script, runner)
        with mock.patch.object(nft, "check", check):
            res = nft.apply(st, self.w.runner)
        self.assertTrue(res.get("recreated"))
        self.assertIn(stale, self.w.nft.elements("gw_xports"))
        self.assertTrue(net.xports_settle(self.w.runner, [xray.POOL_SOCKS_PORT]))   # xray dropped it: out
        self.assertNotIn(stale, self.w.nft.elements("gw_xports"))
        # the boot file is the plan ∪ the guarded ports of the config on disk (B4, REV-s1-b3 П1) — not the live table
        boot = re.search(r"set gw_xports \{[^}]*elements = \{([^}]*)\}", util.read_text(nft.paths.nft_file()))
        self.assertIsNotNone(boot)
        self.assertIn(str(xray.POOL_SOCKS_PORT), boot.group(1))
        self.assertNotIn(stale, boot.group(1))


class SurvivedMutantsTest(S1World):
    """REV-s1-b1 П4: M25 — the chains cannot be listed: 0x2fe is blocked, not let through; M4 — the numgen rule is no
    guard; M10 — the table unknown (peek failed): xray waits; M28 — an address in gw_live without gw_route leaves it."""

    def test_m25_unreadable_chains_block_the_spread_mark(self):
        st = state.load()
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            if argv[:4] == [nft.NFT, "-j", "list", "chain"]:
                return Result(argv, 1, "", "Operation not permitted")
            return real(argv, input=input, **kw)
        live = peek.read(self.w.runner)                                # peek has no rules: spread_ok lists the chains
        self.assertTrue(net.spread_ok(self.w.runner, live))
        with mock.patch.object(self.w.runner, "run", run):
            self.assertFalse(net.spread_ok(self.w.runner, live))
            net.update_map(self.w.runner, st, live=live, elems={IP: net.MARK_SPREAD})
        self.assertNotIn(IP, self.w.nft.elements("gw_route"))
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))

    def test_m4_the_numgen_rule_is_no_guard(self):
        live = nft.read_live(self.w.runner)
        self.assertTrue(net.spread_guarded(live))
        pre = live["rules"]["gw_pre"]
        i = next(i for i, r in enumerate(pre) if len(r) == 2 and (r[1].get("mangle") or {}).get("value")
                 == exits.MARK_BLOCK and net._is_mark_match(r[0], {"meta": {"key": "mark"}}, net.MARK_SPREAD))
        numgen = copy.deepcopy(pre[i])
        numgen[1]["mangle"]["value"] = {"map": {"key": {"numgen": {"mode": "random", "mod": 2, "offset": 0}},
                                                "data": {"set": [[0, 0x101], [1, 0x102]]}}}
        bad = copy.deepcopy(live)
        bad["rules"]["gw_pre"][i] = numgen                             # «по соединениям» drawn, its guard lost
        self.assertFalse(net.spread_guarded(bad))

    def test_m10_unknown_table_xray_waits(self):
        self.round()
        collect.MEM.reset()                                            # the first tick draws xray
        calls = []
        with mock.patch.object(peek, "read", mock.Mock(side_effect=KaskadError("nft -j failed"))), \
                mock.patch.object(xray, "apply", lambda *a, **kw: calls.append(1) or {}):
            res = self.round()
        self.assertEqual(calls, [])
        self.assertIn("nft -j failed", res["exits"].get("error") or "")

    def test_m28_live_without_route_leaves_live(self):
        st = state.load()
        self.w.nft.run_script("delete element ip kaskad gw_route { %s }\n" % IP, True)
        self.assertIn(IP, self.w.nft.elements("gw_live"))
        self.assertFalse(net.del_element(self.w.runner, st, IP))
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))
