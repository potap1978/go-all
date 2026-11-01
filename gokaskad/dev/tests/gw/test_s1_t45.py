"""The merge of T4 and T5 (stage 1, SPEC-stage1-exits §2.2, §2.4, §5.2): every render and apply of xray goes by the
decision of the collector (REV-s1-t5 С1), the ports of xray's own inbounds — the set gw_xports, refilled before xray
opens them (З3), «group:<gid>» executed as its group, the red line «нет интернета» and the scheme by the decision
(REV-s1-t4 С1, С2)."""

import json
from unittest import mock

from kaskad import nft, paths, secrets, state
from kaskad.gw import exits, net, ops, policy as P, xray
from kaskad.panel import scheme
from tests.fakes_gw import register_dummies
from tests.gw.test_collect_s1 import S1World


class RenderByDecisionTest(S1World):
    def test_render_without_a_decision_is_the_one_of_stage_1(self):
        """С1: None — ``exits.decision(st)``, never the config of 3.3.5 (no fail-block, no probe-<id> there)."""
        st = state.load()
        pool = exits.pool(self.w.runner)
        sec = secrets.load()
        cfg = xray.render(st, sec, pool)
        self.assertEqual(cfg, xray.render(st, sec, pool, (), exits.decision(st, pool_exits=pool)))
        self.assertIn(xray.FAIL_BLOCK, [o["tag"] for o in cfg["outbounds"]])
        self.assertIn(xray.probe_tag(self.vid), [i["tag"] for i in cfg["inbounds"]])
        self.assertNotEqual(cfg, xray._render_335(st, sec, pool))
        from tests import realbin
        real = realbin.xray_check(cfg)
        if real is not None:
            self.assertTrue(real[0], real[1][-1500:])

    def test_apply_without_a_decision_draws_by_the_decision(self):
        st = state.load()
        seen = {}
        real = xray.render

        def rd(st, sec, pool, addrs=(), decision=None):
            seen["decision"] = decision
            return real(st, sec, pool, addrs, decision)
        with mock.patch.object(xray, "render", rd):
            xray.apply(st, secrets.load(), self.w.runner)
        self.assertIsNotNone(seen["decision"])
        self.assertIn("states", seen["decision"])

    def test_a_change_of_the_gateway_notes_its_number_and_fingerprint(self):
        """``ops._apply`` (a device added in setUp) -> ``apply_now``: the collector does not draw the same again."""
        done = xray.applied()
        self.assertIsNotNone(done.get("fp"))
        self.assertIsNotNone(done.get("seq"))

    def test_rules_apply_system_draws_by_the_decision(self):
        """С1: «Правила» of the panel (``rules.ops.apply_system``) — the decision, a number of the render, its
        fingerprint; never the config of 3.3.5 (a restart, probes refused, the pool on a swing)."""
        from kaskad.rules import apply as rapply, ops as rops
        seen = {}

        def xa(st, sec, runner, pool, **kw):
            seen.update(kw)
            return {}
        with mock.patch.object(xray, "apply", xa), mock.patch.object(nft, "apply", lambda *a, **kw: {}), \
                mock.patch.object(rapply, "ensure_kernel", lambda *a, **kw: {"action": "none"}):
            rops.apply_system(self.w.runner)
        self.assertIn("states", seen.get("decision") or {})
        self.assertIsInstance(seen.get("seq"), int)
        self.assertTrue(seen.get("fp"))


class GroupRouteXrayTest(S1World):
    def test_group_route_of_an_adapter_is_its_group(self):
        """С1: an inbound of xray whose rules go by the route (the base adapter) — the device's target by the decision:
        «group:G» through the exit of G, never block, never the balancer of the whole pool."""
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": {
            "tag": "socks-in", "listen": "0.0.0.0", "port": 1080, "protocol": "socks"}}))
        st = state.load()
        doc = P.default_doc("compat")
        doc["groups"][0]["tiers"][0]["exits"] = [self.vid]
        doc["groups"].append(P.default_group("g-abc123", "abroad", "compat", name="Стриминг", exits=[self.aid]))
        st["gateway"]["exit_policy"] = doc
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        d = st["gateway"]["devices"][0]
        d["access"].append("socks5")
        d["route"] = {"via": "group:g-abc123", "fail": "block"}
        pool = exits.pool(self.w.runner)
        dec = exits.decision(st, pool_exits=pool, alive={self.vid, self.aid})
        cfg = xray.render(st, secrets.load(), pool, (), dec)
        mine = [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["socks-in"] and r.get("user")]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].get("outboundTag"), exits.target(st, d, dec)["xray"]["outboundTag"])
        self.assertEqual(mine[0]["outboundTag"], exits.tag(self.aid))
        self.real_check(cfg)

    def test_group_route_without_a_device_is_the_balancer_of_its_group(self):
        """A rule drawn by the route alone (no device): «group:G» — a balancer of the exits of G, never block."""
        from kaskad.gw.inbounds import base
        register_dummies(("socks5", {"proto": "tcp", "default_port": 1080, "ifaces": [], "xray_inbound": {
            "tag": "socks-in", "listen": "0.0.0.0", "port": 1080, "protocol": "socks"}}))
        st = state.load()
        doc = P.default_doc("compat")
        doc["groups"][0]["tiers"][0]["exits"] = [self.vid]
        doc["groups"].append(P.default_group("g-abc123", "abroad", "compat", name="Стриминг", exits=[self.aid]))
        doc["groups"].append(P.default_group("g-def456", "abroad", "compat", name="Пустая", exits=[]))
        st["gateway"]["exit_policy"] = doc
        st["gateway"]["inbounds"]["socks5"] = {"enabled": True, "port": 1080}
        st["gateway"]["devices"][0]["access"].append("socks5")

        def by_route(self_, st_, sec_, target=None):
            return [dict({"type": "field", "inboundTag": ["socks-in"], "user": [g]},
                         **target({"via": "group:" + g, "fail": f})) for g, f in (("g-abc123", "block"),
                                                                                  ("g-def456", "direct"))]
        pool = exits.pool(self.w.runner)
        with mock.patch.object(base.DummyInbound, "xray_routing", by_route):
            cfg = xray.render(st, secrets.load(), pool, (), exits.decision(st, pool_exits=pool))
        got = dict((r["user"][0], r) for r in cfg["routing"]["rules"] if r.get("inboundTag") == ["socks-in"]
                   and r.get("user"))
        self.assertEqual(got["g-abc123"].get("balancerTag"), "gw-g-g-abc123")
        bal = dict((b["tag"], b) for b in cfg["routing"]["balancers"])
        self.assertEqual(bal["gw-g-g-abc123"]["selector"], [exits.tag(self.aid)])
        self.assertEqual(got["g-def456"].get("outboundTag"), "direct")          # no exit in G: its fail
        self.real_check(cfg)

    def real_check(self, cfg):
        from tests import realbin
        real = realbin.xray_check(cfg)                 # the real xray -test, when it is here (tools/fetch_test_bins.sh)
        if real is not None:
            self.assertTrue(real[0], real[1][-1500:])


class XportsTest(S1World):
    def plan_ports(self, st):
        return [str(x) for x in net.xports_of(net.plan(st, self.w.runner))]

    def test_update_map_refills_the_ports_only_when_asked(self):
        """З3: the set gw_xports — by its content (verify), refilled by ``update_map(ports=True)``."""
        st = state.load()
        want = self.plan_ports(st)
        self.assertIn(str(xray.probe_port(next(e for e in exits.pool(self.w.runner) if e["id"] == self.vid))), want)
        self.assertEqual(sorted(self.w.nft.elements("gw_xports"), key=int), want)
        self.w.nft.run_script("flush set ip kaskad gw_xports\n", True)
        live = nft.read_live(self.w.runner)
        self.assertTrue([p for p in net.verify(st, live) if "gw_xports" in p])     # by the content, the rules are 1
        net.update_map(self.w.runner, st)
        self.assertEqual(self.w.nft.elements("gw_xports"), [])                  # a plan without ports: left alone
        net.update_map(self.w.runner, st, ports=True)
        self.assertEqual(sorted(self.w.nft.elements("gw_xports"), key=int), want)
        self.assertFalse([p for p in net.verify(st, nft.read_live(self.w.runner)) if "gw_xports" in p])

    def test_the_collector_closes_a_probe_port_before_xray_opens_it(self):
        st = state.load()
        port = str(xray.probe_port(next(e for e in exits.pool(self.w.runner) if e["id"] == self.vid)))
        self.w.nft.run_script("flush set ip kaskad gw_xports\n", True)
        seen = {}

        def xa(st, sec, runner, pool, **kw):
            seen["xports"] = list(self.w.nft.elements("gw_xports"))
            return {}
        with mock.patch.object(xray, "apply", xa):
            self.round()
        self.assertIn(port, seen["xports"])
        self.assertTrue(st)


class NoExitsTest(S1World):
    def rows(self, usable):
        return [{"id": self.vid, "usable": self.vid in usable}, {"id": self.aid, "usable": self.aid in usable}]

    def group_world(self):
        st = state.load()
        doc = P.default_doc("compat")
        doc["groups"][0]["tiers"][0]["exits"] = [self.vid]
        doc["groups"].append(P.default_group("g-abc123", "abroad", "compat", name="Стриминг", exits=[self.aid]))
        st["gateway"]["exit_policy"] = doc
        return st

    def test_group_route_by_its_own_exits(self):
        """REV-s1-t4 С1: the default route «group:G» — no false «нет интернета» while an exit of G works."""
        st = self.group_world()
        st["gateway"]["routes"]["default"] = {"via": "group:g-abc123", "fail": "block"}
        self.assertIsNone(ops.no_exits_problem(st, self.rows({self.aid}), live_age=1))
        got = ops.no_exits_problem(st, self.rows({self.vid}), live_age=1)       # G has nothing working
        self.assertEqual((got or {}).get("kind"), "no_exits")

    def test_exit_route_falls_to_its_group(self):
        """§2.2 (отличие 4 §1.3): «exit:E», E down — the other exits of E's group carry the devices."""
        st = self.group_world()
        st["gateway"]["exit_policy"]["groups"][0]["tiers"][0]["exits"] = [self.vid, self.aid]
        st["gateway"]["exit_policy"]["groups"] = st["gateway"]["exit_policy"]["groups"][:1]
        st["gateway"]["routes"]["default"] = {"via": "exit:" + self.vid, "fail": "block"}
        self.assertIsNone(ops.no_exits_problem(st, self.rows({self.aid}), live_age=1))
        self.assertEqual((ops.no_exits_problem(st, self.rows(set()), live_age=1) or {}).get("kind"), "no_exits")


class SchemeNowTest(S1World):
    def test_scheme_now_is_the_decision(self):
        """REV-s1-t4 С2: the device pinned to X, X «в запасе», the other exit «снят» — the scheme says X, as
        exits.target (gw_route) and trace do; never «блок»."""
        st = state.load()
        d = st["gateway"]["devices"][0]
        pool = exits.pool(self.w.runner)
        snap = {"exits": {self.vid: {"state": "standby"}, self.aid: {"state": "off"}}}
        with open(paths.pins_json(), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "groups": {"main": {"pins": {d["id"]: {
                "exit": self.vid, "tier": "t1", "since": 0, "why": "new"}}}}}, f)
        dec = exits.decision(st, snap, pool)
        t = exits.target(st, d, dec)
        self.assertEqual(t["exit"], self.vid)
        doc = scheme.build(st, pool, snap, scheme.load_layout())
        rows = [r for n in doc["nodes"] if n["type"] == "user" for r in n["data"]["devices"] if r["id"] == d["id"]]
        self.assertEqual(rows[0]["now"]["exit"], self.vid)
        self.assertEqual(rows[0]["now"]["target"], "exit:" + self.vid)
