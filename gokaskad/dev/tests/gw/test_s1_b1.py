"""Stage 1, step B1 — the safety of the network after the merge of T4+T5 (SEC-s1-t45, REV-s1-t45; SPEC-todo, the record
SEC-s1-t45): the guard of «по соединениям» always in the table (С-1), gw_xports never shrunk by a failed render (З-1)
and present before xray of stage 1 opens its SOCKS (З1/З-2), only added to by the table and settled by xray after it
dropped an inbound (П2), the lock of xray 0600 (З-3), curl of gRPC without proxies (З-4), del_element in one
transaction (П-1), the healed config tested again (REV З3 M13), gw_ruonly between full applies (REV З3 M16), «xray не
нужен» noted (П1). Each test fails on the code before B1."""

import os
import stat
import shutil
import tempfile
import unittest
from unittest import mock

from kaskad import nft, secrets, state
from kaskad.errors import KaskadError
from kaskad.gw import collect, exits, net, ruguard, xray, xraygrpc
from kaskad.rules import apply as rules_apply
from tests.gw.s1_xray import S1Case
from tests.gw.test_collect_s1 import S1World

IP = "10.77.1.10"


def _nft_scripts(case):
    seen = []
    real = case.w.runner.run

    def run(argv, input=None, **kw):
        if argv[:1] == ["nft"] and input:
            seen.append(input)
        return real(argv, input=input, **kw)
    return seen, mock.patch.object(case.w.runner, "run", run)


def _drop_rules(case, chain, text_has):
    """Take the rules of ``chain`` whose JSON has ``text_has`` out of the fake table (a table drawn before B1)."""
    rules = case.w.nft.table()["chains"][chain]["rules"]
    for i in reversed(range(len(rules))):
        if text_has(rules[i]["expr"]):
            case.w.nft.drop_rule(chain, i)


def _is_spread_guard(expr):
    return any(isinstance(e, dict) and (e.get("match") or {}).get("right") == net.MARK_SPREAD for e in expr)


class SpreadGuardTest(S1World):
    """С-1: 0x2fe has no ip rule — its guard («0x2fe → блок» in gw_pre, «ct mark 0x2fe drop» in gw_fwd) is in every
    table drawn, the mode on or off; a table without it never takes an element 0x2fe."""

    def test_the_guard_is_drawn_without_the_mode(self):
        st = state.load()
        p = net.plan(st, self.w.runner)
        self.assertNotIn("spread", p)                                   # nobody «по соединениям»
        rules = dict(net.rules(st, p))
        self.assertIn(net.SPREAD_GUARD_PRE, rules["gw_pre"])
        self.assertIn(net.SPREAD_GUARD_FWD, rules["gw_fwd"])
        self.assertFalse([r for r in rules["gw_pre"] if "numgen" in r])
        self.assertTrue(net.spread_guarded(nft.read_live(self.w.runner)))      # the live table (the collector's)
        self.assertTrue(net.spread_ok(self.w.runner))                          # by two listings (peek has no rules)

    def test_an_element_between_full_applies_meets_the_guard(self):
        """SEC (а): the policy changed without a full apply — update_map puts 0x2fe; the rules are there already."""
        st = state.load()
        self.assertTrue(net.update_map(self.w.runner, st, elems={IP: net.MARK_SPREAD}))
        self.assertEqual(self.w.nft.elements("gw_route"), {IP: net.MARK_SPREAD})
        live = nft.read_live(self.w.runner)
        self.assertTrue(net.spread_guarded(live))

    def test_a_table_without_the_guard_keeps_the_element_out(self):
        st = state.load()
        _drop_rules(self, "gw_pre", _is_spread_guard)
        _drop_rules(self, "gw_fwd", _is_spread_guard)
        self.assertFalse(net.spread_ok(self.w.runner))
        net.update_map(self.w.runner, st, elems={IP: net.MARK_SPREAD})
        self.assertNotIn(IP, self.w.nft.elements("gw_route"))
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))                  # blocked, not out directly
        # SEC (б): a hook of IKEv2 with the mark — refused, the address out of gw_route and gw_live
        self.assertFalse(net.set_element(self.w.runner, st, IP, net.MARK_SPREAD))
        self.assertNotIn(IP, self.w.nft.elements("gw_route"))
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))
        # half a guard is no guard
        nft.apply(st, self.w.runner)
        _drop_rules(self, "gw_fwd", _is_spread_guard)
        self.assertFalse(net.spread_ok(self.w.runner))

    def test_a_hook_with_the_guard_in_place_puts_the_mark(self):
        st = state.load()
        self.assertTrue(net.set_element(self.w.runner, st, IP, net.MARK_SPREAD))
        self.assertEqual(self.w.nft.elements("gw_route")[IP], net.MARK_SPREAD)


class XportsFailClosedTest(S1World):
    """З-1: a render that fails never shrinks gw_xports to the API of the bot — every port of xray closed instead."""

    def test_a_failed_render_closes_every_port(self):
        st = state.load()
        before = set(self.w.nft.elements("gw_xports"))
        self.assertIn(str(xray.POOL_SOCKS_PORT), before)
        with mock.patch.object(xray, "render", mock.Mock(side_effect=KaskadError("no secrets"))):
            nft.apply(st, self.w.runner)                               # verifies inside: no drift reported
            live = nft.read_live(self.w.runner)
            self.assertFalse([p for p in net.verify(st, live) if "gw_xports" in p])
        got = set(self.w.nft.elements("gw_xports"))
        self.assertLessEqual(before, got)
        for port in (xray.METRICS_PORT, xray.STATS_PORT, xray.POOL_SOCKS_PORT, xray.PROBE_PORT_BASE,
                     xray.PROBE_PORT_BASE + exits.MAX_VLESS_INDEX, xray.EGRESS_API_PORT):
            self.assertIn(str(port), got)
        self.assertNotIn(str(xray.DNS_PORT), got)                     # dnsmasq goes there

    def test_verify_wants_the_closed_set_not_the_bot_api_alone(self):
        st = state.load()
        self.w.nft.run_script("flush set ip kaskad gw_xports\nadd element ip kaskad gw_xports { 10851 }\n", True)
        with mock.patch.object(xray, "render", mock.Mock(side_effect=KaskadError("no secrets"))):
            probs = [p for p in net.verify(st, nft.read_live(self.w.runner)) if "gw_xports" in p]
        self.assertTrue(probs)


class XportsBeforeXrayTest(S1World):
    """З1 / З-2: a table of 3.3.5 (no gw_xports, no rule of gw_out) — the collector applies the whole table before the
    render of xray of stage 1 opens the SOCKS of the probes and of the pool; no table — xray waits."""

    def table_335(self):
        rules = self.w.nft.table()["chains"]["gw_out"]["rules"]
        for i in reversed(range(len(rules))):
            if "@gw_xports" in str(rules[i]["expr"]):
                self.w.nft.drop_rule("gw_out", i)
        self.w.nft.run_script("delete set ip kaskad gw_xports\n", True)
        self.assertNotIn("gw_xports", self.w.nft.table()["sets"])

    def test_the_whole_table_before_xray(self):
        self.table_335()
        collect.MEM.reset()
        seen = {}

        def xa(st, sec, runner, pool, **kw):
            t = self.w.nft.table()
            seen["set"] = sorted(t["sets"]["gw_xports"]["elems"]) if "gw_xports" in t["sets"] else None
            seen["rule"] = any("@gw_xports" in str(r["expr"]) for r in t["chains"]["gw_out"]["rules"])
            return {}
        with mock.patch.object(xray, "apply", xa):
            self.round()
        self.assertIsNotNone(seen.get("set"), "xray of stage 1 applied without gw_xports in the table")
        self.assertTrue(seen["rule"])
        port = str(xray.probe_port(next(e for e in exits.pool(self.w.runner) if e["id"] == self.vid)))
        self.assertIn(port, self.w.nft.elements("gw_xports"))

    def test_no_xray_when_the_table_cannot_be_applied(self):
        self.table_335()
        collect.MEM.reset()
        calls = []
        with mock.patch.object(xray, "apply", lambda *a, **kw: calls.append(1) or {}), \
                mock.patch.object(nft, "apply", mock.Mock(side_effect=KaskadError("nft refused"))):
            res = self.round()
        self.assertEqual(calls, [])
        self.assertIn("nft refused", res["exits"].get("error") or "")
        with mock.patch.object(xray, "apply", lambda *a, **kw: calls.append(1) or {}):
            self.round(dt=collect.XPORTS_RETRY_S)                      # after the pause (B3, REV-s1-b1 П1): the
                                                                       # table, then xray
        self.assertEqual(calls, [1])
        self.assertIn("gw_xports", self.w.nft.table()["sets"])


class XportsCloseFirstTest(S1World):
    """П2: a port leaves gw_xports only after xray dropped its inbound — the table only adds; xray.apply settles."""

    def test_the_table_never_takes_a_port_out(self):
        st = state.load()
        stale = str(xray.PROBE_PORT_BASE + 9)                          # the probe of an exit just removed
        self.w.nft.run_script("add element ip kaskad gw_xports { %s }\n" % stale, True)
        net.update_map(self.w.runner, st, ports=True)
        self.assertIn(stale, self.w.nft.elements("gw_xports"))
        nft.apply(st, self.w.runner)                                   # the full apply of a new pool: kept too
        self.assertIn(stale, self.w.nft.elements("gw_xports"))
        self.assertFalse([p for p in net.verify(st, nft.read_live(self.w.runner)) if "gw_xports" in p])
        self.assertTrue(net.xports_settle(self.w.runner, [xray.POOL_SOCKS_PORT]))
        self.assertEqual(sorted(self.w.nft.elements("gw_xports"), key=int), ["10851", "12699"])

    def test_xray_apply_closes_before_and_settles_after(self):
        st = state.load()
        sec = secrets.load()
        pool = exits.pool(self.w.runner)
        stale = str(xray.PROBE_PORT_BASE + 9)
        self.w.nft.run_script("flush set ip kaskad gw_xports\nadd element ip kaskad gw_xports { %s }\n" % stale, True)
        order = []
        real_add, real_rename = net.xports_add, os.rename

        def add(runner, ports):
            order.append(("add", sorted(ports)))
            return real_add(runner, ports)

        def rename(a, b):
            if b == xray.config_path():
                order.append(("config", None))
            return real_rename(a, b)
        try:
            os.unlink(xray.config_path())
        except OSError:
            pass
        with mock.patch.object(net, "xports_add", add), mock.patch.object(xray.os, "rename", rename):
            xray.apply(st, sec, self.w.runner, pool, seq=99, fp="b1")
        cfg_ports = xray.guarded_ports(xray.render(st, sec, pool, (), exits.decision(st, pool_exits=pool)))
        self.assertEqual(order[:2], [("add", cfg_ports), ("config", None)])   # closed before xray reads the file
        got = sorted(self.w.nft.elements("gw_xports"), key=int)
        self.assertEqual(got, [str(x) for x in sorted(set(cfg_ports) | {xray.EGRESS_API_PORT})])
        self.assertNotIn(stale, got)                                   # settled once xray runs the new config

    def test_not_needed_noted_and_settled(self):
        """REV П1: xray not needed — stopped once, noted (no ``disable --now`` every 10 s), its ports out of the set."""
        st = state.load()
        calls = []
        real = self.w.runner.run

        def run(argv, input=None, **kw):
            if argv[:3] == ["systemctl", "disable", "--now"]:
                calls.append(argv)
            return real(argv, input=input, **kw)
        with mock.patch.object(xray, "needed", lambda *a: False), mock.patch.object(self.w.runner, "run", run):
            for _ in range(4):
                self.round()
        self.assertLessEqual(len(calls), 1)
        self.assertEqual(self.w.nft.elements("gw_xports"), ["10851"])


class XrayLockTest(S1Case):
    def test_the_lock_is_0600(self):
        """З-3: a lock any user may open is a lock any user may hold (and freeze xray)."""
        from kaskad import paths
        p = paths.xray_apply_lock()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a"):
            pass
        os.chmod(p, 0o644)                                             # left by an older version
        f = xray._lock(1)
        try:
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        finally:
            xray._unlock(f)
        os.unlink(p)
        old = os.umask(0o022)
        try:
            xray._unlock(xray._lock(1))
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)


class HealTestedAgainTest(S1Case):
    """REV З3 M13: the config drawn again after a healed pool goes through ``xray -test`` as well; refused — nothing
    replaced, no restart."""

    def text(self, p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_the_redrawn_config_is_tested(self):
        self.node()
        xray.apply(self.st, self.sec, self.w.runner, self.pool, self.dec(), seq=1)
        before = self.text(xray.config_path())
        self.w.xray.fail = "Failed to start: bad\n"
        n0 = len(self.w.xray.tested)
        with mock.patch.object(xray, "_healed", lambda runner, x: True):
            with self.assertRaises(KaskadError):
                xray.apply(self.st, self.sec, self.w.runner, self.pool, self.dec(quota_off=[self.bid]), seq=2)
        self.assertEqual(len(self.w.xray.tested), n0 + 2)              # the first and the healed one
        self.assertEqual(self.text(xray.config_path()), before)


class RuonlyBetweenAppliesTest(S1World):
    """REV З3 M16: a tick without a new pool — the device went to the fallback block of its group with «Россия
    напрямую»: update_map puts it into gw_ruonly and gw_live (Russia only), not just into gw_route."""

    def test_update_map_fills_ruonly(self):
        self.round()
        self.assertEqual(self.w.nft.elements("gw_ruonly"), [])
        real = exits.targets

        def targets(st, dec, leases=None, **kw):
            got = real(st, dec, leases=leases, **kw)
            if IP in got:
                got[IP] = dict(got[IP], mark=exits.MARK_BLOCK, step="last_block")
            return got
        with mock.patch.object(exits, "targets", targets), \
                mock.patch.object(ruguard, "allowed", return_value=True), \
                mock.patch.object(rules_apply, "rd", lambda st, leases=None: [IP]):     # D-136: as with the consent
            res = self.round()
        self.assertFalse(res["exits"].get("pool_changed"))
        self.assertEqual(self.w.nft.elements("gw_ruonly"), [IP])
        self.assertIn(IP, self.w.nft.elements("gw_live"))
        self.assertEqual(self.w.nft.elements("gw_route")[IP], exits.MARK_BLOCK)


class DelElementTest(S1World):
    def test_live_and_route_in_one_transaction(self):
        """П-1: never an address in gw_live without gw_route (0x2ff, and 0x201 to Russia from gw_rd)."""
        st = state.load()
        self.assertIn(IP, self.w.nft.elements("gw_live"))
        seen, patch = _nft_scripts(self)
        with patch:
            self.assertTrue(net.del_element(self.w.runner, st, IP))
        route = [s for s in seen if "gw_route" in s]
        self.assertEqual(len(route), 1)
        self.assertIn("gw_live", route[0])
        self.assertLess(route[0].index("gw_live"), route[0].index("gw_route"))
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))
        self.assertNotIn(IP, self.w.nft.elements("gw_route"))
        self.assertNotIn(IP, self.w.nft.elements("gw_ruonly"))

    def test_a_blocked_address_and_an_absent_one(self):
        st = state.load()
        self.assertIn(IP, self.w.nft.elements("gw_live"))
        # B1 (было до правки): a live address blocked by a hook left gw_live (the node's services, Russia from gw_rd)
        self.assertTrue(net.set_element(self.w.runner, st, IP, exits.MARK_BLOCK))
        self.assertEqual(self.w.nft.elements("gw_route")[IP], exits.MARK_BLOCK)
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))
        self.assertTrue(net.del_element(self.w.runner, st, IP))
        self.assertNotIn(IP, self.w.nft.elements("gw_route"))
        self.assertFalse(net.del_element(self.w.runner, st, IP))       # nothing there: False, nothing breaks
        self.w.nft.run_script("delete set ip kaskad gw_ruonly\n", True)  # a table before stage 1
        self.round()
        self.assertTrue(net.del_element(self.w.runner, st, IP))
        self.assertNotIn(IP, self.w.nft.elements("gw_live"))


FAKE_CURL = """#!/bin/sh
if [ "$1" = "-V" ]; then echo "Features: AsynchDNS HTTP2 IPv6 SSL"; exit 0; fi
echo "$@" > "%(log)s"
env > "%(log)s.env"
cat > /dev/null
echo "< grpc-status: 0" >&2
exit 0
"""


class GrpcNoProxyTest(unittest.TestCase):
    def test_no_proxy_no_curlrc(self):
        """З-4: the body carries the auth of a user — never to a proxy of the shell, never with a ~/.curlrc."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        xraygrpc._curl_ok.clear()
        self.addCleanup(xraygrpc._curl_ok.clear)
        curl = os.path.join(tmp, "curl")
        log = os.path.join(tmp, "log")
        with open(curl, "w") as f:
            f.write(FAKE_CURL % {"log": log})
        os.chmod(curl, 0o755)
        env = {"http_proxy": "http://192.0.2.1:3128", "HTTPS_PROXY": "http://192.0.2.1:3128",
               "ALL_PROXY": "socks5://192.0.2.1:1080", "all_proxy": "socks5://192.0.2.1:1080",
               "CURL_HOME": tmp}
        with mock.patch.dict(os.environ, env):
            ok, why = xraygrpc.call("127.0.0.1:10854", b"x", curl=curl)
        self.assertTrue(ok, why)
        with open(log) as f:
            argv = f.read().split()
        self.assertEqual(argv[0], "-q")                                # first, or curl reads ~/.curlrc
        self.assertEqual(argv[argv.index("--noproxy") + 1], "*")
        with open(log + ".env") as f:
            got = f.read()
        self.assertNotIn("192.0.2.1", got)
        self.assertNotIn("CURL_HOME", got)


if __name__ == "__main__":
    unittest.main()
